# RAG Service

> **Documentation status**: This is a capability overview. Comprehensive API reference and configuration guide are pending.

A semantic search and knowledge management service backed by ChromaDB. Runs as a FastAPI application communicating over Unix domain socket (default: `/tmp/universal-protocol/rag.sock`) or TCP.

## Architecture

Index time:

1. **Chunking** — files are split into semantically coherent chunks using target+pad sizing with paragraph overlap and heading injection. Code files use tree-sitter AST-based chunking.
2. **Source hashing** — plain SHA-256 of file bytes is stored as `source_hash` on every chunk (PDF, Markdown, HTML, etc.). This hash serves as the universal join key to the `articles` table for query-time metadata enrichment.
3. **Knowledge extraction** — the `rag-extraction` LLM pipeline extracts entities, types, facets, topics, and relations from each chunk. Results are stored in both ChromaDB metadata and a SQLite-backed property inverted index.
4. **Contextualization** — on by default (omit `contextualize_model` or set it to a model ID). Per-chunk LLM-generated context prefixes are prepended only for embedding; stored document text stays unchanged. Set `contextualize_model: ""` to disable. Improves retrieval when chunks share overlapping vocabulary.
5. **Embedding** — chunks (with context prefix when contextualization ran) are embedded via the configured local embedding model (default: `qwen3-embedding-8b`) through the Gateway and stored in ChromaDB with cosine similarity.
6. **Pending journal** — tracks in-flight indexing operations. On restart, interrupted files are re-indexed before the watcher starts, eliminating dangling pointers.

Search time:

7. **Vector search** — ChromaDB cosine similarity retrieves top-k candidate chunks.
8. **Property boost** — entity/topic/relation matches from the property index apply a configurable score boost to matching chunks (hybrid structured+vector search).
9. **Article enrichment** — unique `source_hash` values from the result set are batch-looked up in the `articles` table. Matching article metadata (`article_title`, `article_authors`, `article_venue`, `article_published_date`, `article_doi`) is merged into each chunk's metadata dict.
10. **Recency scoring** — additive recency weight based on `published_date` (preferred for research papers) or `indexed_at` timestamps. Naive ISO timestamps are normalized to UTC before scoring to avoid timezone subtraction errors.
11. **BM25 sidecar merge** — sparse BM25 candidates are merged with dense vector results via mini-RRF. Chroma fetch payloads are length-normalized (pad/trim) and invalid metadata rows are skipped to avoid strict zip failures.

The pipeline layer (`rag-context`, `rag-answer`, `rag-answer-deep`) handles query rewriting, facet-driven retrieval, RRF multi-query merge, reranking, and answer generation on top of this service. See [Pipeline Layer](#pipeline-layer-rag-context) below.

## Pipeline Layer (`rag-context`)

The `rag-context` pipeline runs on top of the RAG service and implements corpus-grounded multi-stage retrieval. It is exposed as a virtual model ID (`rag-context`, `rag-answer`, `rag-answer-deep`) through Stargate.

### Pre-Retrieval: Corpus-Grounded Query Rewriting

Most RAG systems either search the raw query (misses lexical variants) or rely on unconstrained LLM rewriting (hallucinates terms not in the corpus). This pipeline does **corpus-grounded rewriting**:

1. **`suggest_terms`** — extracts candidate vocabulary from the raw query
2. **`filter_corpus_hints`** — validates candidates against *actually indexed vocabulary* via the property index. Terms that don't appear in the corpus are discarded before reaching any LLM.
3. **`analyze_scope`** — classifies the query into retrieval scopes using validated terms; applies scope anchors only for single-scope predictions (multi-scope implies broader intent)
4. **`predict_facets`** — decomposes the query into named retrieval sub-topics with corpus-grounded vocabulary
5. **`refine_facets`** — second-pass prediction: given first-pass facets, surfaces deeper or more specific terms from parametric knowledge (e.g. discovers `Zettelkasten`, `NEPOMUK` from `personal_knowledge_management` facet)
6. **`generate_rewrites`** — produces embedding-optimized sub-queries that *must* include validated terms
7. **`generate_hyde`** — generates a hypothetical answer passage with `must_include` constraints to stay grounded

The **co-occurrence filter** (`filter_hints_by_cooccurrence`) is the key grounding mechanism — it checks whether a candidate hint term actually appears in chunks alongside the query terms, preventing vocabulary hallucination.

Scope vocabulary (the `scope_vocabulary` table in `rag_metadata.db`) separates terms into `academic`, `practitioner`, and `specification` registers so rewrites target the correct register for each query type (e.g. `PKG` → academic, `Obsidian` → practitioner).

### Retrieval: Two-Pool Hybrid

Retrieval runs two parallel pools:

**Pool A — Dense + Sparse hybrid**: standard hybrid search (ChromaDB cosine similarity + BM25 sidecar), one query per rewrite/HyDE variant, merged via RRF.

**Pool B — Named-entity sparse-only**: for each facet from `refine_facets`, constructs an OR-joined FTS5 query from all terms in that facet and dispatches with `sparse_only=True` (bypasses dense embedding entirely). This surfaces exact-match named-entity hits (e.g. `NEPOMUK OR PIMO OR Zettelkasten`) that dense embedding dilutes or misses.

Post-RRF scoring adjustments:
- Pool B chunks receive a `facet_pool_score_boost` multiplier (default 1.5×) with **lateral source habituation**: the highest-scoring chunk from a given source gets the full boost; subsequent chunks from the same source are inhibited (÷boost), preventing any single source from monopolizing boosted slots
- **Global source habituation**: applied across all merged chunks — subsequent chunks from any already-represented source receive exponentially decayed scores, ensuring coverage breadth
- **Pool B source swap**: if a source has any Pool B hit, all Pool A chunks from that same source are evicted (the sparse named-entity hit subsumes the semantic hit from the same document)

### Reranking

A sliding-window LLM reranker (`rerank_assemble`) takes the assembled Pool A + Pool B chunks and re-orders them. The reranker receives the `refine_facets` output as explicit context in its prompt, guiding it to prefer chunks covering multiple facets simultaneously and to penalize generic survey content that mentions a domain without engaging with the named entities.

Score fusion: `final = prior_weight × rrf_score + (1 − prior_weight) × llm_score` with bounded movement (`rerank_max_movement`) to prevent large rank inversions from a single window judgment.

### Pipeline IDs

| Model ID | Description |
|----------|-------------|
| `rag-context` | Returns assembled context chunks (no answer generation) |
| `rag-answer` | Context retrieval + grounded answer generation |
| `rag-answer-deep` | Context retrieval + iterative refinement + answer generation |
| `consult-*` | Domain-specialized consultation pipelines (researcher, architect, prompt-engineer) |

Pipeline configuration: `pipelines/rag/rag_context_v1/rag-context-v1.yaml`

---

## Supported Formats

| Format | Chunking Strategy |
|--------|-------------------|
| Markdown (`.md`) | Structure-aware: headers, paragraphs, code blocks |
| Code (`.py`) | Tree-sitter AST-based with metadata extraction |
| PDF (`.pdf`) | Native text extraction via `pymupdf4llm`, content-hash dedup |
| EPUB / Ebook | Text extraction and structural chunking |
| HTML (`.html`, `.htm`) | BeautifulSoup + markdownify normalization |
| Plain text (`.txt`) | Paragraph-based chunking |

## API Endpoints

### Search

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /search` | POST | Semantic search — accepts `query`, `top_k`, `scope`, `source_prefixes`, `recency_weight`, `max_distance` |
| `POST /chunks_by_index` | POST | Fetch chunks by source path and chunk index (neighbor expansion) |

### Indexing

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /index` | POST | Index a single file |
| `POST /reindex` | POST | Reindex a single file (re-chunk, re-embed, re-extract) |
| `POST /index_directory` | POST | Index all supported files in a directory |
| `POST /reindex_directory` | POST | Reindex directory — removes stale sources, reindexes changed files |
| `POST /clear_directory` | POST | Remove all chunks under a directory path |
| `POST /clear` | POST | Clear entire collection |

### Article Metadata

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /article` | POST | Upsert article citation metadata (merge semantics — non-empty fields overwrite, empty fields preserve) |

Also available as MCP tool `rag_upsert_article` via Stargate passthrough (`POST /api/v1/rag/article`).

### Corpus Management

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /scopes` | GET | List the named scope registry |
| `GET /sources` | GET | List indexed source paths (optional prefix filter) |
| `GET /source` | GET | Get all chunks for a source path |
| `GET /stats` | GET | Collection statistics (chunk count) |

### Knowledge Extraction

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /extraction/failed` | GET | Chunks with failed extraction attempts (optional source filter) |
| `GET /extraction_export` | GET | Bulk export extracted properties (optional prefix, include_text flag) |

### Monitoring

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /watch/status` | GET | File watcher status |

## Configuration

Config file: `~/.gateway/rag.yaml`

```yaml
watch_directories:
  - path: /path/to/documents
    extensions: [.md, .txt, .pdf]
    recursive: true
    chunk_tokens: 1024        # target chunk size in tokens (~4 chars/token)
    exclude: [".git", "node_modules"]

scopes:
  research:
    prefixes: ["/path/to/papers/"]
    description: "Research papers and articles"
  codebase:
    prefixes: ["/path/to/code/"]
    description: "Source code"
  all:
    union: true               # aggregates all scope prefixes
    description: "Everything"

embedding_model: qwen3-embedding-8b-q8-0-40960-cpu

knowledge_extraction:
  pipeline: rag-extraction
  property_boost_factor: 0.5
  max_extraction_attempts: 3
  extraction_model: ""        # model mismatch triggers re-extraction

automatic_indexing_enabled: true
post_index_enforcement: strict   # strict | warn — strict returns 503 until enrichment is current
```

### Key Options

| Option | Purpose |
|--------|---------|
| `watch_directories` | Directories to watch with inotify; auto-reindex on file changes |
| `scopes` | Named retrieval scopes — consumers reference by name; `union: true` aggregates all |
| `chunk_tokens` | Target chunk size per directory (default varies: 1024 for docs, 256 for code) |
| `knowledge_extraction` | LLM extraction config — pipeline, boost factor, retry limits |
| `post_index_enforcement` | `strict` (default): return 503 on search until post-index enrichment watermarks are current. `warn`: log ERROR at startup but continue serving |
| `contextualize_model` | Model ID for per-chunk context generation before embedding. Omit for default (on); set to `""` to disable |
| `reconcile_interval_s` | Seconds between watcher reconcile sweeps. Default 300 (5 min). 0 = disabled |

## Storage

| Path | Contents |
|------|----------|
| `~/.rag/store/chroma/` | ChromaDB persistent vector data |
| `~/.rag/store/rag_metadata.db` | SQLite metadata store (property index, corpus_hints, scope_vocabulary, articles, watermarks, schema_version) |

### `articles` Table

The `articles` table in `rag_metadata.db` is the runtime source of truth for citation metadata. It is keyed by `source_path` and joined to search results via the `content_hash` column (which matches the `source_hash` stored on Chroma chunks).

| Column | Type | Purpose |
|--------|------|---------|
| `source_path` | TEXT PK | Absolute path to the source file |
| `filename` | TEXT | Filename only (e.g. `paper.pdf`) |
| `title` | TEXT | Article title |
| `authors` | TEXT | Authors (comma-separated) |
| `venue` | TEXT | Publication venue |
| `published_date` | TEXT | Publication date (ISO format) |
| `doi` | TEXT | Digital Object Identifier |
| `abstract` | TEXT | Article abstract |
| `scope` | TEXT | Retrieval scope (default `all`) |
| `content_hash` | TEXT | Plain SHA-256 of file bytes — join key to `source_hash` on chunks |
| `subdirectory` | TEXT | Subdirectory within the corpus root |

Population: `python scripts/populate-articles.py` reads `docs/research/article_registry.yaml` and upserts validated entries. Idempotent, does not require the RAG service to be running.

Article metadata is **not** baked into chunks at index time. Instead, the search handler enriches results at query time by joining `source_hash` → `content_hash`.

### Clean-Slate Reindex

```bash
rm -rf ~/.rag/*                          # wipe both ChromaDB and rag_metadata.db
python scripts/populate-articles.py      # recreate DB + seed articles table
./manage                                 # start services — indexes all files with source_hash
```

## Post-Index Enrichment

Indexing handles chunk extraction, embedding, knowledge extraction, and per-chunk `is_bibliography` tagging automatically. After a large corpus refresh, three manual enrichment steps rebuild derived artifacts:

1. **Corpus hints** (`python -m services.rag.corpus_hints`) — aggregates terms from the property index per scope into the `corpus_hints` table in `rag_metadata.db`.
2. **Scope vocabulary** (`scripts/rag/classify_vocabulary.py`) — LLM-classifies corpus hint terms into register categories and writes to the `scope_vocabulary` table in `rag_metadata.db`.
3. **Bibliography classification** (`scripts/rag/classify_bibliography.py`) — LLM-classifies chunks and writes boolean metadata keys (`is_bibliography`, `is_non_intelligible`) into ChromaDB. Resumable by metadata key.

Each step stamps a watermark in `rag_metadata.db`. When `post_index_enforcement: strict` (default), the service returns 503 on search requests until all watermarks are current relative to the last reindex. In `warn` mode, an ERROR is logged at startup but search continues.

Verify watermark freshness:

```bash
sqlite3 ~/.rag/store/rag_metadata.db "SELECT * FROM watermarks ORDER BY step"
```

Full procedure: [Post-Index Refresh Runbook](../../tasks/runbooks/rag-post-index-refresh.md).

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/populate-articles.py` | Seed `articles` table from curated YAML registry (`--dry-run` supported) |
| `scripts/rag/classify_vocabulary.py` | LLM-classify scope vocabulary registers from corpus hints |
| `scripts/rag/classify_bibliography.py` | LLM-classify chunk-level bibliography/noise metadata |
| `scripts/rag/ingest-arxiv` | Ingest arXiv papers into the RAG corpus |

## Events

Events are published to the Event Service via `universal_event_bus`. Query with `scripts/query-events --op noise-profile --minutes 5`.

Covers indexing operations, search queries, extraction progress, watcher status.

Extraction failure observability:

- If extraction returns an invalid payload shape, each chunk is recorded as a failed attempt in the property index and emits `rag_extraction_failed`.
- During recovery, rows with invalid metadata are dropped and logged with counts before rerun.
- Batch timeout paths emit `rag_extraction_batch_timed_out`; permanently exhausted chunks emit `rag_extraction_permanently_skipped`.

## Deep Context for Agents

For subsystem-specific investigation, reference these paths directly:

| Area | Path | What it covers |
|------|------|---------------|
| Pipeline structure | `pipelines/rag/rag_context_v1/rag-context-v1.yaml` | Step sequence, model refs, generation params |
| Pipeline handlers | `pipelines/rag/rag_context_v1/handlers/` | Corpus-grounded rewriting, retrieval, reranking |
| Metadata DB schema | `services/rag/property_index.py` | Schema versioning, migration, all metadata tables |
| Corpus hints flow | `services/rag/corpus_hints.py` | Hint generation, DB read/write, co-occurrence filtering |
| Vocabulary classification | `scripts/rag/classify_vocabulary.py` | LLM-based register classification |
| Enrichment runbook | `tasks/runbooks/rag-post-index-refresh.md` | Operator post-index workflow |
| RAG config | `services/rag/config.py` | `RagConfig` dataclass, YAML parsing |
| MCP RAG tools | `services/mcp-server/tools/rag.py` | `rag_search`, `rag_list_scopes`, prefix passthrough |

## Key Files

| File | Responsibility |
|------|---------------|
| `rag_service/main.py` | FastAPI app assembly and lifecycle wiring |
| `rag_service/api.py` | Router and endpoint registration |
| `rag_service/search.py` | Search execution flow used by `/search` |
| `rag_service/indexing.py` | Indexing/reindexing implementation |
| `rag_service/state.py` | Shared runtime state for the modular service |
| `rag_service/lifecycle.py` | Startup/shutdown orchestration |
| `config.py` | Configuration dataclasses and YAML parsing |
| `search_scope.py` | Scoped search, property boost, recency scoring |
| `chunkers.py` | Format-aware chunking (markdown, text) |
| `chunker_ast_metadata.py` | Tree-sitter AST chunking for code |
| `property_index.py` | SQLite inverted index for extracted properties |
| `knowledge_extractor.py` | LLM extraction orchestration |
| `extraction_wiring.py` | Pipeline integration for extraction |
| `article_registry.py` | Article citation metadata management |
| `corpus_hints.py` | Scope-specific vocabulary hints |
| `metadata_boost.py` | Score boosting from extracted metadata |
| `watcher_manager.py` | Inotify file watching and reconciliation |
| `embeddings.py` | Embedding model client (via Gateway) |
| `admin_routes.py` | Administrative API endpoints |
