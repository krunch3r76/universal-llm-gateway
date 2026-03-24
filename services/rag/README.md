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

The `rag-context` pipeline (v2.0) runs on top of the RAG service and implements fast, zero-LLM-call retrieval with cross-encoder reranking. Exposed as a virtual model ID (`rag-context`, `rag-answer`, `rag-answer-deep`) through Stargate. Average end-to-end latency: **~1.6s**.

### Pipeline Steps

| Step | Handler | LLM? | Description |
|------|---------|-------|-------------|
| `direct_scope` | `rag_direct_scope_v1` | No | Fixed scope resolution (default `"all"`, overridable via `scope_override`) |
| `generate_hyde` | `generate` | Yes (conditional) | Hypothetical document embedding — only runs when `hyde_enabled: true` (default: off) |
| `retrieve_assemble` | `rag_multi_retrieve_v1` | No | Batched embedding + concurrent IDF expansion + two-pool retrieval + RRF merge |
| `rerank_assemble` | `rag_rerank_assemble_v1` | Depends on mode | Cross-encoder (default, ~100ms) or generative sliding-window reranker |

### Pre-Retrieval: Inline Term Expansion

Query factoring and IDF expansion run concurrently with dense retrieval (zero added latency). Pure functions in `term_expansion.py`:

1. **Phrase extraction** — decomposes the query into sub-phrases for independent sparse-only BM25 queries (pool B)
2. **IDF-weighted corpus expansion** — queries the property index for terms that co-occur with the query's most discriminative words, surfacing vocabulary the user didn't use but the corpus contains

These run via `asyncio.to_thread` alongside the pool A embedding calls, eliminating the ~300ms serial bottleneck of the old `expand_terms` pipeline step.

### Retrieval: Two-Pool Hybrid with Batched Embedding

All query embeddings are computed in a **single batched GPU pass** via the `/embed_batch` endpoint before dispatch, then passed as pre-computed vectors to individual `/search` calls. This eliminates sequential per-query GPU embedding passes.

**Pool A — Dense + Sparse hybrid**: ChromaDB cosine similarity + BM25 sidecar, one query per original + HyDE variant, merged via RRF. Uses pre-computed embeddings.

**Pool B — Named-entity sparse-only**: for each facet from inline IDF expansion, constructs an OR-joined FTS5 query and dispatches with `sparse_only=True` (bypasses embedding). Surfaces exact-match hits that dense embedding dilutes.

Post-RRF scoring adjustments:
- Pool B chunks receive a `facet_pool_score_boost` multiplier (default 1.5×) with **lateral source habituation**: the highest-scoring chunk from a given source gets the full boost; subsequent chunks from the same source are inhibited (÷boost)
- **Global source habituation**: subsequent chunks from any already-represented source receive exponentially decayed scores, ensuring coverage breadth
- **Pool B source swap**: if a source has any Pool B hit, all Pool A chunks from that same source are evicted

### Reranking

Default mode: **cross-encoder** (`rerank_mode: cross_encoder`). A `BAAI/bge-reranker-v2-m3` cross-encoder scores (query, passage) pairs in a single forward pass — ~80-175ms for 14 passages on GPU. Scores are fused with RRF prior: `final = prior_weight × rrf_score + (1 − prior_weight) × cross_encoder_score`, bounded by `rerank_max_movement`.

Alternative mode: **generative** (`rerank_mode: generative`). Sliding-window LLM reranker with facet-aware prompting. Higher quality ceiling but 4-7s latency.

The cross-encoder model loads lazily on first call and stays resident (~550MB GPU memory). See `services/rag/cross_encoder.py`.

### Key Pipeline Options

| Option | Default | Effect |
|--------|---------|--------|
| `rerank_mode` | `cross_encoder` | `generative` for LLM sliding-window reranker |
| `rerank_enabled` | `true` | `false` skips reranking entirely |
| `hyde_enabled` | `false` | `true` adds one LLM call for hypothetical document embedding |
| `max_idf_terms` | `8` | `0` skips IDF expansion |
| `max_discriminative` | `4` | Number of query words used as IDF seeds |
| `rerank_max_candidates` | `14` | Passages sent to cross-encoder |
| `rerank_prior_weight` | `0.70` | Weight of RRF score vs reranker score |
| `rerank_max_movement` | `3` | Max rank positions a chunk can shift during reranking |

### Pipeline IDs

| Model ID | Description |
|----------|-------------|
| `rag-context` | Returns assembled context chunks (no answer generation). Default for MCP `rag_search`. |
| `rag-context-rewrite` | Legacy: 10-step LLM rewriting chain. Not routed by default — preserved for comparison. |
| `rag-answer` | Context retrieval + grounded answer generation |
| `rag-answer-deep` | Context retrieval + iterative refinement + answer generation |
| `consult-*` | Domain-specialized consultation pipelines (researcher, architect, prompt-engineer) |

Pipeline configuration: `pipelines/rag/rag_context_v1/rag-context-v1-direct.yaml`

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
| `POST /search` | POST | Semantic search — accepts `query`, `top_k`, `scope`, `source_prefixes`, `recency_weight`, `max_distance`, optional `query_embedding` (pre-computed) |
| `POST /embed_batch` | POST | Batch-embed multiple query texts in a single GPU forward pass. Returns list of embedding vectors. |
| `POST /rerank` | POST | Score (query, passage) pairs via cross-encoder model. Returns relevance scores per passage. |
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

### Source Deletion

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `DELETE /source?path=...` | DELETE | Remove a single source from all 4 surfaces (ChromaDB, FTS, property index, articles table) |
| `DELETE /directory?path=...` | DELETE | Remove all sources under a directory prefix from all surfaces |

Responses: `DELETE /source` returns `source`, `chunks_deleted`, `fts_removed`, `properties_removed`, `article_deleted`. `DELETE /directory` returns `path`, `sources_deleted`, `chunks_deleted`, `fts_removed`, `articles_deleted`.

Also available as MCP tools: `rag_delete_source` (`DELETE /api/v1/rag/source?path=...`) and `rag_delete_directory` (`DELETE /api/v1/rag/directory?path=...`).

### Diagnostics

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /orphaned_articles` | GET | Articles with no corresponding indexed chunks (metadata-only rows) |
| `GET /indexing/status` | GET | Unified pending/failure/watcher/chunk-count operational status (bounded sample via `sample_limit`) |

Also available as MCP tool `rag_orphaned_articles` via Stargate passthrough (`GET /api/v1/rag/orphaned_articles`).

### Corpus Management

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /scopes` | GET | List the named scope registry (prefixes, description per scope) |
| `POST /scopes` | POST | Register a scope at runtime — adds to live config, optional watcher registration, persists to `~/.gateway/rag.yaml`. Body: `name`, `prefixes`, `description?`, `watch?`, `force?`. 409 if scope exists unless `force: true`. |
| `GET /coverage` | GET | Per-scope, per-prefix indexed file counts and last-indexed timestamps |
| `GET /sources` | GET | List indexed source paths (optional prefix filter) |
| `GET /source` | GET | Get all chunks for a source path |
| `GET /stats` | GET | Collection statistics (chunk count) |

The MCP tool `rag_list_scopes` merges `GET /scopes` with `GET /coverage`: each scope in `details` includes `indexed_files` and `status` (`"indexed"` or `"empty"`).

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

embedding_model: qwen3-embedding-8b-q8-0-4096

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
| `reconcile_interval_s` | Base seconds between watcher reconcile sweeps. Default 300 (5 min). 0 = disabled. Reconcile uses the same worker pool as initial reindex; when a sweep recovers files, the next sweep runs after 30 s (busy interval), otherwise after the full interval |

## Storage

| Path | Contents |
|------|----------|
| `~/.rag/store/chroma/` | ChromaDB persistent vector data |
| `~/.rag/store/rag_metadata.db` | SQLite metadata store (property index, pending journal, failed extractions, indexed_sources, corpus_hints, scope_vocabulary, articles, watermarks, schema_version) |

### `indexed_sources` Table

`indexed_sources` stores one row per source path with the last evaluated filesystem
state used by the mtime-first unchanged fast path:

| Column | Type | Purpose |
|--------|------|---------|
| `source` | TEXT PK | Absolute source path |
| `mtime_ns` | INTEGER | Last observed filesystem modification time |
| `size_bytes` | INTEGER | Last observed file size |
| `extraction_schema_version` | INTEGER | Invalidates cache when extraction schema changes |
| `extraction_model` | TEXT | Invalidates cache when extraction model changes |
| `updated_at` | TEXT | Audit timestamp for the cache row |

Operational note: the first sweep after deploying this change seeds `indexed_sources`
for already-indexed files. Subsequent startup and reconcile sweeps skip unchanged
files via `stat()` instead of reading file bytes or querying Chroma.

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

Population: **`scripts/backfill_article_metadata.py`** reads `docs/research/article_registry.yaml`, maps subdirectory→scope (e.g. `rag-systems`→`rag_systems`, `prompting`→`small_llm_prompting`), and upserts via Stargate `POST /api/v1/rag/article`. Idempotent; requires Stargate on :9999. **`scripts/populate-articles.py`** does the same from the YAML but sets scope to `all` for every entry; supports `--dry-run` and direct RAG URL (e.g. UDS). Use backfill for scope-aware corpus seeding.

Article metadata is **not** baked into chunks at index time. Instead, the search handler enriches results at query time by joining `source_hash` → `content_hash`.

## Article Metadata Lifecycle

- Indexing maintains structural article identity (`source_path`, `filename`, `scope`, `content_hash`, `subdirectory`) in the `articles` table.
- Watcher-driven file deletes preserve article rows so move detection can migrate curated metadata when the same bytes reappear at a new path.
- Admin `DELETE /source` and `DELETE /directory` remain fully destructive across `articles` and `indexed_sources`.
- Query-time enrichment still joins chunk `source_hash` to `articles.content_hash`; no chunk reindex is needed for curated metadata edits.
- `POST /article` updates SQLite first and then refreshes the in-memory basename cache from the canonical row.

### Clean-Slate Reindex

```bash
rm -rf ~/.rag/*                              # wipe both ChromaDB and rag_metadata.db
python scripts/backfill_article_metadata.py  # seed articles with subdirectory→scope mapping (Stargate :9999)
./manage                                     # start services — indexes all files with source_hash
```

### Corpus Drift Repair

Use `scripts/repair-rag-article-lifecycle.py` (default dry-run) to audit
`indexed_sources`/`articles` drift after deploying lifecycle changes.

- `--apply` repairs indexed sources that are missing article rows.
- `--apply --prune-missing` also deletes article-only rows whose files are gone.
- Existing-on-disk metadata-only rows are reported but preserved.

## Post-Index Enrichment

Indexing handles chunk extraction, embedding, knowledge extraction, and per-chunk `is_noise` / `noise_reason` tagging (heuristic; `normalize_noise_metadata` aligns legacy `is_bibliography` and fills missing `noise_reason` on upsert). Pipeline fixtures and snapshots may keep `is_bibliography: false` only — readers use `chunk_metadata_is_noise` which accepts both keys. After a large corpus refresh, three manual enrichment steps rebuild derived artifacts:

1. **Corpus hints** (`python -m services.rag.corpus_hints`) — aggregates terms from the property index per scope into the `corpus_hints` table in `rag_metadata.db`.
2. **Scope vocabulary** (`scripts/rag/classify_vocabulary.py`) — LLM-classifies corpus hint terms into register categories and writes to the `scope_vocabulary` table in `rag_metadata.db`. Fail-closed: aborts without writing if any scope fails classification.
3. **Noise classification** (`scripts/rag/classify_noise.py`) — LLM-classifies chunks: `--target bibliography` writes `is_noise`; `non_intelligible` writes `is_non_intelligible`; `--target describe` writes `noise_description` for review. Resumable by metadata key.

Each step stamps a watermark in `rag_metadata.db`. When `post_index_enforcement: strict` (default), the service returns 503 on search requests until all watermarks are current relative to the last reindex. In `warn` mode, an ERROR is logged at startup but search continues.

Verify watermark freshness:

```bash
sqlite3 ~/.rag/store/rag_metadata.db "SELECT * FROM watermarks ORDER BY step"
```

Full procedure: [Post-Index Refresh Runbook](../../tasks/runbooks/rag-post-index-refresh.md).

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/backfill_article_metadata.py` | Seed `articles` from YAML with subdirectory→scope mapping (Stargate; idempotent) |
| `scripts/populate-articles.py` | Seed `articles` from YAML with scope=`all` (`--dry-run`, direct RAG URL supported) |
| `scripts/rag/classify_vocabulary.py` | LLM-classify scope vocabulary registers from corpus hints |
| `scripts/rag/classify_noise.py` | LLM-classify chunk noise metadata (`is_noise`, `is_non_intelligible`, `noise_description`) |
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
| Pipeline structure | `pipelines/rag/rag_context_v1/rag-context-v1-direct.yaml` | Step sequence, model refs, generation params |
| Pipeline handlers | `pipelines/rag/rag_context_v1/handlers/` | Retrieval, reranking, scope resolution |
| Term expansion | `pipelines/rag/rag_context_v1/term_expansion.py` | IDF expansion, phrase extraction (shared pure functions) |
| Cross-encoder | `services/rag/cross_encoder.py` | Cross-encoder model loading and inference |
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
| `watcher_manager.py` | Inotify file watching; reconciliation sweeps (worker pool, adaptive interval) |
| `embeddings.py` | Embedding model client (via Gateway); batch embedding support |
| `cross_encoder.py` | Cross-encoder reranking model (BAAI/bge-reranker-v2-m3) |
| `admin_routes.py` | Administrative API endpoints |
