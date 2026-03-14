# RAG Service

> **Documentation status**: This is a capability overview. Comprehensive API reference and configuration guide are pending.

A semantic search and knowledge management service backed by ChromaDB. Runs as a FastAPI application communicating over Unix domain socket (default: `/tmp/universal-protocol/rag.sock`) or TCP.

## Architecture

Index time:

1. **Chunking** — files are split into semantically coherent chunks using target+pad sizing with paragraph overlap and heading injection. Code files use tree-sitter AST-based chunking.
2. **Knowledge extraction** — the `rag-extraction` LLM pipeline extracts entities, types, facets, topics, and relations from each chunk. Results are stored in both ChromaDB metadata and a SQLite-backed property inverted index.
3. **Contextualization** — on by default (omit `contextualize_model` or set it to a model ID). Per-chunk LLM-generated context prefixes are prepended only for embedding; stored document text stays unchanged. Set `contextualize_model: ""` to disable. Improves retrieval when chunks share overlapping vocabulary.
4. **Embedding** — chunks (with context prefix when contextualization ran) are embedded via the configured local embedding model (default: `qwen3-embedding-8b`) through the Gateway and stored in ChromaDB with cosine similarity.
5. **Pending journal** — tracks in-flight indexing operations. On restart, interrupted files are re-indexed before the watcher starts, eliminating dangling pointers.

Search time:

6. **Vector search** — ChromaDB cosine similarity retrieves top-k candidate chunks.
7. **Property boost** — entity/topic/relation matches from the property index apply a configurable score boost to matching chunks (hybrid structured+vector search).
8. **Recency scoring** — additive recency weight based on `published_date` (preferred for research papers) or `indexed_at` timestamps. Naive ISO timestamps are normalized to UTC before scoring to avoid timezone subtraction errors.
9. **BM25 sidecar merge** — sparse BM25 candidates are merged with dense vector results via mini-RRF. Chroma fetch payloads are length-normalized (pad/trim) and invalid metadata rows are skipped to avoid strict zip failures.

The pipeline layer (`rag-context`, `rag-answer`, `rag-answer-deep`) handles query rewriting, RRF multi-query merge, and answer generation on top of this service.

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
corpus_hints_path: ~/.gateway/corpus_hints.yaml
article_registry_path: ~/.gateway/article_registry.yaml
```

### Key Options

| Option | Purpose |
|--------|---------|
| `watch_directories` | Directories to watch with inotify; auto-reindex on file changes |
| `scopes` | Named retrieval scopes — consumers reference by name; `union: true` aggregates all |
| `chunk_tokens` | Target chunk size per directory (default varies: 1024 for docs, 256 for code) |
| `knowledge_extraction` | LLM extraction config — pipeline, boost factor, retry limits |
| `corpus_hints_path` | Scope-specific vocabulary hints for retrieval tuning |
| `article_registry_path` | Citation metadata (title, authors, venue, DOI, published_date) per file |

## Storage

| Path | Contents |
|------|----------|
| `~/.rag/store/` | ChromaDB persistent data |
| `~/.rag/store/property_index.db` | SQLite property inverted index |

## Events

Event stream: `/tmp/rag-events/current.jsonl`

Covers indexing operations, search queries, extraction progress, watcher status.

Extraction failure observability:

- If extraction returns an invalid payload shape, each chunk is recorded as a failed attempt in the property index and emits `rag_extraction_failed`.
- During recovery, rows with invalid metadata are dropped and logged with counts before rerun.
- Batch timeout paths emit `rag_extraction_batch_timed_out`; permanently exhausted chunks emit `rag_extraction_permanently_skipped`.

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
