# RAG Service Architecture

<!-- GENERATED:START inventory_sha=4c6a06a51ed0 generated=2026-07-19 -->
_Generated from docstrings, signatures, and imports; claims reflect what the source **declares**, not verified runtime behavior. doc-generate verifies doc<->docstring consistency, not docstring<->behavior truth._

## Scope

This document covers the `services/rag` subsystem: a FastAPI-based Retrieval-Augmented Generation service responsible for document ingestion, chunking, embedding, indexing, knowledge extraction, and hybrid search. The service coordinates ChromaDB (vector store), SQLite (metadata and property index, FTS5), and Stargate (LLM gateway) to provide searchable, enriched document corpora.

---

## Module Inventory

| Path | Description |
|---|---|
| `services/rag/__init__.py` | Package root (no docstring) |
| `services/rag/admin_routes/__init__.py` | Admin/CRUD routes for the RAG service. Operational endpoints for indexing, reindexing, source lifecycle cleanup, extraction exports, and coverage/status reporting. Coordinates ChromaDB data with SQLite-backed metadata surfaces. |
| `services/rag/admin_routes/_directory_routes.py` | Directory-level delete route: `DELETE /directory` |
| `services/rag/admin_routes/_extraction_export.py` | Bulk extraction export route: `GET /extraction_export` |
| `services/rag/admin_routes/_helpers.py` | Shared helpers and validators for admin routes |
| `services/rag/admin_routes/articles.py` | Article metadata and source deletion routes |
| `services/rag/admin_routes/failures.py` | Indexing failure management routes: `/indexing_failures` |
| `services/rag/admin_routes/indexing.py` | File and directory indexing routes: `/index`, `/reindex`, `/index_directory`, etc. |
| `services/rag/admin_routes/status.py` | Status and monitoring routes: `/indexing/status`, `/coverage`, `/source-status`, `/watch/status` |
| `services/rag/admission_gate/__init__.py` | Event-driven admission gate for batch contextualization workers |
| `services/rag/admission_gate/_constants.py` | `AdmissionGate` configuration constants |
| `services/rag/admission_gate/_io.py` | Async I/O: startup snapshot, WebSocket subscriber loop, first-burst emission |
| `services/rag/admission_gate/_signals.py` | Signal dispatch: map incoming event signals to gate open/close transitions |
| `services/rag/admission_gate/_state_changes.py` | Gate open/close state mutations and first-burst emission |
| `services/rag/admission_gate/gate.py` | Event-driven admission gate for batch contextualization workers. Subscribes to Stargate coordination-role signals over the Event Service WebSocket. |
| `services/rag/article_registry.py` | Article registry helpers for YAML migration and DB-backed runtime lookups |
| `services/rag/chunk_filters.py` | Noise chunk detection for index-time tagging and retrieval fallback |
| `services/rag/chunker_ast_metadata.py` | AST metadata helpers for Python chunk annotation |
| `services/rag/chunkers/__init__.py` | Document chunking for RAG indexing |
| `services/rag/chunkers/_sizing.py` | Chunk sizing constants and shared parsers |
| `services/rag/chunkers/code_chunking.py` | Code AST and line-based chunking |
| `services/rag/chunkers/epub_html.py` | EPUB and HTML chunking |
| `services/rag/chunkers/markdown_pdf.py` | Markdown and PDF chunking |
| `services/rag/chunkers/office_dispatch.py` | Office formats and extension dispatch |
| `services/rag/chunkers/paragraph_utils.py` | Paragraph splitting utilities and `Chunk` type |
| `services/rag/config/__init__.py` | RAG service configuration: YAML parsing and dataclass definitions. Loads `~/.gateway/rag.yaml` into typed dataclasses. |
| `services/rag/config/_loader.py` | RAG config loading and persistence: `load_config` and `save_scope` |
| `services/rag/config/_models.py` | RAG configuration dataclasses and module-level defaults |
| `services/rag/config/_parsing.py` | RAG config YAML parsing: watch directories, scopes, knowledge extraction |
| `services/rag/contextualize.py` | LLM-based contextual embedding enrichment for RAG chunks |
| `services/rag/contextualize_cache.py` | Pure planner and merger for the contextualize prefix cache |
| `services/rag/corpus_hints/__init__.py` | Corpus hints: term co-occurrence statistics for vocabulary-aware retrieval |
| `services/rag/corpus_hints/cli.py` | CLI entry for one-shot corpus hints generation and Chroma backfill |
| `services/rag/corpus_hints/constants.py` | Shared defaults, blocklists, and term-filter regex for corpus hints |
| `services/rag/corpus_hints/cooccurrence.py` | Chunk- and document-level co-occurrence filtering for hint terms |
| `services/rag/corpus_hints/formatting.py` | Format corpus hints and register vocabulary for prompt injection |
| `services/rag/corpus_hints/freshness.py` | Scope freshness hashing and watch-path overlap for corpus hint refresh |
| `services/rag/corpus_hints/loaders.py` | Read corpus hints and scope vocabulary from the metadata SQLite database |
| `services/rag/corpus_hints/term_scoring.py` | Term noise filtering and IDF-style scoring for corpus hint selection |
| `services/rag/corpus_hints/update.py` | Persist discriminative corpus hints from property-index term statistics |
| `services/rag/directory_ops.py` | Shared directory-level source discovery and cleanup helpers for RAG |
| `services/rag/embeddings/__init__.py` | Embedding model client for RAG indexing and search |
| `services/rag/embeddings/batch_post.py` | Gateway POST batching with retry, split-on-500, and zero-vector fallback |
| `services/rag/embeddings/chunk_embed.py` | Index-time chunk embedding with token-aware batching |
| `services/rag/embeddings/constants.py` | Embedding client tunables and scope instruction templates |
| `services/rag/embeddings/errors.py` | Embedding client exception types |
| `services/rag/embeddings/health.py` | Startup health gating for the configured embedding model |
| `services/rag/embeddings/model_id.py` | Model ID parsing and batch token limits for embedding requests |
| `services/rag/embeddings/query_embed.py` | Search-time query embedding with instruction prefixes and bounded retry |
| `services/rag/embeddings/runtime.py` | Shared mutable runtime state for the embedding HTTP client |
| `services/rag/entity_admission/__init__.py` | Fail-closed entity-admission gate for entity-curated RAG watch roots |
| `services/rag/entity_admission/_constants.py` | `EntityAdmissionGate` configuration constants |
| `services/rag/entity_admission/_io.py` | Async I/O: admitted-set snapshot/refresh, WS subscriber, backstop loop |
| `services/rag/entity_admission/_signals.py` | Signal application: react to `cortex.entity.source.changed` |
| `services/rag/entity_admission/gate.py` | Fail-closed entity-admission gate |
| `services/rag/entity_merging.py` | Cross-chunk merging of entities, relations, and topics for structured RAG context |
| `services/rag/events/__init__.py` | RAG event factories package |
| `services/rag/events/admission.py` | RAG admission gate event factories |
| `services/rag/events/articles.py` | RAG article metadata event factories |
| `services/rag/events/extraction.py` | RAG extraction event factories |
| `services/rag/events/extraction_admission.py` | RAG extraction admission (coordination) event factories |
| `services/rag/events/extraction_queue.py` | RAG extraction queue event factories |
| `services/rag/events/indexing/__init__.py` | RAG indexing and storage event factories (package re-exports) |
| `services/rag/events/indexing/chunk_signals.py` | RAG indexing event factories — chunk-level contextualization signals |
| `services/rag/events/indexing/contextualization.py` | RAG indexing event factories — file-level contextualization flow |
| `services/rag/events/indexing/contextualize_cache.py` | RAG indexing event factories — contextualize cache read/write/gc |
| `services/rag/events/indexing/directory_batch.py` | RAG indexing event factories — directory batch and property index rebuild |
| `services/rag/events/indexing/failure_tracking.py` | RAG indexing event factories — indexing failure and retry tracking |
| `services/rag/events/indexing/file_lifecycle.py` | RAG indexing event factories — file lifecycle transitions |
| `services/rag/events/indexing/html_normalization.py` | RAG indexing event factories — HTML normalization events |
| `services/rag/events/indexing/storage_pipeline.py` | RAG indexing event factories — embed, chroma, property, commit, hints pipeline |
| `services/rag/events/lifecycle.py` | RAG lifecycle and watcher event factories |
| `services/rag/events/query.py` | RAG scope, search, and corpus-hint event factories |
| `services/rag/extraction/__init__.py` | Helpers for the async decoupled knowledge extraction worker |
| `services/rag/extraction/capacity_envelope.py` | Classify HTTP error envelopes as capacity-class vs structural |
| `services/rag/extraction/chroma_source.py` | Run extraction for one Chroma source |
| `services/rag/extraction/property_entries.py` | Map extracted knowledge into property-index quads |
| `services/rag/extraction/record_failure.py` | Persist extraction failure and emit queue event |
| `services/rag/extraction/worker_loop.py` | Main asyncio loop for the decoupled extraction worker |
| `services/rag/extraction_admission.py` | Observation-driven admission gate for the RAG extraction worker |
| `services/rag/extraction_worker.py` | Async background worker for decoupled knowledge extraction |
| `services/rag/fts_index.py` | SQLite FTS5 full-text index for BM25 sparse retrieval (Pool B backend) |
| `services/rag/indexing_failure_classifier.py` | Pure indexing failure classification (no rag_service package import) |
| `services/rag/indexing_helpers.py` | Chroma source migration, file hashing, and duplicate detection helpers |
| `services/rag/knowledge_extractor.py` | LLM-based structured knowledge extraction via the rag-extraction pipeline |
| `services/rag/metadata_boost.py` | Post-RRF metadata boost for RAG retrieval |
| `services/rag/model_availability_tracker.py` | Aggregate model availability for RAG embedding and extraction paths |
| `services/rag/models/__init__.py` | Request/response models for the RAG service API (barrel re-export) |
| `services/rag/models/articles_ops.py` | Article metadata, embed/rerank, corpus hints refresh, and coverage DTOs |
| `services/rag/models/extraction_admin.py` | Extraction export, chunk admin, indexing failures, and extraction queue DTOs |
| `services/rag/models/search_index.py` | Search, indexing, stats, and scope listing DTOs for the RAG HTTP API |
| `services/rag/property_index/__init__.py` | RAG `PropertyIndex`: SQLite property/chunk/queue metadata |
| `services/rag/property_index/_spec.py` | SQLite-backed property inverted index for structured RAG |
| `services/rag/property_index/mixin_01.py` – `mixin_09_articles_ops.py` | `PropertyIndex` method chunks (SLOC split); assembled via multiple inheritance |
| `services/rag/property_index/sql_block.py` | Property index: DDL / migration SQL strings |
| `services/rag/rag_service/__init__.py` | Package entrypoint for the RAG FastAPI service |
| `services/rag/rag_service/api.py` | FastAPI router definitions for the RAG service |
| `services/rag/rag_service/background_tasks.py` | Track asyncio tasks for coordinated shutdown |
| `services/rag/rag_service/dependency_activation.py` | Retry Stargate-backed dependency activation until watcher runtime can start |
| `services/rag/rag_service/extraction_runtime.py` | Watcher-independent extraction worker runtime |
| `services/rag/rag_service/indexing/__init__.py` | Indexing package — file-level index/delete funnel and phase helpers |
| `services/rag/rag_service/indexing/article_sync.py` | Article sync phase: orphan detection and content-hash mismatch check |
| `services/rag/rag_service/indexing/chroma.py` | ChromaDB upsert helpers for the indexing pipeline |
| `services/rag/rag_service/indexing/commit.py` | Commit phase for the indexing pipeline |
| `services/rag/rag_service/indexing/contextualize.py` | Contextualization phase for the indexing pipeline |
| `services/rag/rag_service/indexing/contextualize_cache.py` | Contextualize-cache load/store and partial-failure persistence for indexing |
| `services/rag/rag_service/indexing/delete.py` | File deletion and extraction-queue helpers for the indexing pipeline |
| `services/rag/rag_service/indexing/embed.py` | Embed phase: chunk preparation, noise tagging, contextualization, Chroma upsert, FTS |
| `services/rag/rag_service/indexing/failure_ops.py` | Failure classification and persistence helpers for the indexing pipeline |
| `services/rag/rag_service/indexing/file_guards.py` | Pre-chunking and post-chunking guard helpers for `_index_file_impl` |
| `services/rag/rag_service/indexing/finalize.py` | Post-commit success path for a completed index funnel run |
| `services/rag/rag_service/indexing/index_file.py` | Indexing and deletion pipeline for RAG chunks |
| `services/rag/rag_service/indexing/source_identity.py` | Shared helpers reused across indexing sub-modules |
| `services/rag/rag_service/lifecycle.py` | RAG service lifecycle orchestration |
| `services/rag/rag_service/lifecycle_constants.py` | Shared constants for RAG lifecycle, watcher startup, and dependency activation |
| `services/rag/rag_service/main.py` | RAG service application assembly |
| `services/rag/rag_service/scope_freshness.py` | Startup / reconcile / watcher automatic scope-freshness repair hooks |
| `services/rag/rag_service/search.py` | Search execution for RAG query endpoints |
| `services/rag/rag_service/source_path_gate.py` | Per-source FIFO serialization for index/delete |
| `services/rag/rag_service/startup_cleanup.py` | Startup reconciliation, orphan / exclusion purges, and watch chunk-token resolution |
| `services/rag/rag_service/state.py` | Shared state and helpers for the RAG service package |
| `services/rag/rag_service/watcher_runtime.py` | Watcher, extraction worker, and post-activation background cleanup |
| `services/rag/search_scope/__init__.py` | Search scope resolution, property boost, BM25 sidecar, and recency sort |
| `services/rag/search_scope/bm25_sidecar.py` | BM25 sparse sidecar merged with dense results via mini-RRF |
| `services/rag/search_scope/prefix_filter.py` | Source-prefix and max-distance filters for search result lists |
| `services/rag/search_scope/property_boost.py` | Property-index distance boost for hybrid search |
| `services/rag/search_scope/recency.py` | Recency-weighted distance adjustment for search ranking |
| `services/rag/search_scope/scope_resolution.py` | Named scope resolution for search requests |
| `services/rag/tier_weighting.py` | Provenance-tier distance weighting for RAG retrieval |
| `services/rag/vocabulary/__init__.py` | LLM scope vocabulary classification and automatic gap repair |
| `services/rag/vocabulary/_categories.py` | Per-category descriptions and default taxonomy for vocabulary classification |
| `services/rag/vocabulary/_classify.py` | LLM-based vocabulary classification: local model and frontier pipeline paths |
| `services/rag/vocabulary/_prompt.py` | Build the system prompt for scope vocabulary classification |
| `services/rag/vocabulary/_repair.py` | Orchestrator: `run_scope_freshness_repair` — refresh hints and vocabulary for stale scopes |
| `services/rag/vocabulary/_scope_helpers.py` | Scope utility helpers: configured scopes map and vocab mode resolution |
| `services/rag/vocabulary/_skill_attribution.py` | Pure helpers for per-skill vocabulary attribution JOIN |
| `services/rag/vocabulary/_stargate.py` | Stargate endpoint URL constants for vocabulary classification |
| `services/rag/watcher_manager/__init__.py` | Inotify file watcher and reconciliation sweep manager for RAG indexing |
| `services/rag/watcher_manager/file_events.py` | Hot-reload file change and delete handlers |
| `services/rag/watcher_manager/initial_reindex.py` | Startup initial reindex sweep for one watch directory |
| `services/rag/watcher_manager/manager.py` | `WatcherManager` core lifecycle and admission gating |
| `services/rag/watcher_manager/protocols.py` | Protocols, constants, and extension helpers for the RAG file watcher |
| `services/rag/watcher_manager/reconcile.py` | Periodic reconciliation sweeps to recover missed files |
| `services/rag/watcher_manager/registration.py` | Inotify watcher registration for configured directories |
| `services/rag/watcher_manager/scope_repair.py` | Scope freshness repair debouncing for `WatcherManager` |

---

## Key Classes

### `AdmissionGate` (`admission_gate/gate.py`)

Per-model admission gate driven by Stargate capacity signals. Maintains one `asyncio.Event` per tracked model; workers await the event before submitting work. State defaults SET (OPEN). Transitions to CLOSED on `capacity.admission.paused`, `model.loading.started`, or `federation.gateway.degraded`. Transitions to OPEN on `capacity.admission.resumed`, `model.loaded`, `model.load.failed`, or `federation.gateway.recovered`. Tracking key is `ModelId.routing_key`.

- `start()` — configure from snapshot then spawn background subscriber; idempotent
- `stop()` — cancel the subscriber task
- `wait_for_admission(model_id, timeout)` — wait until admission is OPEN; returns `True` if OPEN or untracked, `False` on timeout; callers must proceed regardless

### `EntityAdmissionGate` (`entity_admission/gate.py`)

Fail-closed gate for entity-curated RAG watch roots. Maintains an in-memory set of absolute paths backed by some entity's `source_uri`. Defaults EMPTY (fail-closed). Built from a cortex-api REST snapshot, refreshed by `cortex.entity.source.changed` events with a periodic backstop.

- `is_admitted(abspath)` — O(1) membership test
- `is_ready()` — True once ≥1 snapshot/refresh has succeeded
- `snapshot_size()` — current admitted-path count
- `mark_dirty()` — signal that a source.changed event arrived
- `start()` / `stop()` — lifecycle

### `ExtractionAdmissionGate` (`extraction_admission.py`)

Observation-driven admission gate for the RAG extraction worker. Tracks pipeline iteration signals, gateway signals, and model signals to open/close admission.

- `wait_for_admission(timeout)` — return True when admission opens before timeout
- `is_closed()` / `active_reasons()` — state inspection

### `FtsIndex` (`fts_index.py`)

FTS5 full-text index for BM25 sparse retrieval. Shares the SQLite connection and `SequentialExecutor` with `PropertyIndex`. Provides `insert_batch`, `remove_batch`, `search`, and `search_scoped` operations.

- `attach(conn, seq)` — bind to an existing connection and executor
- `search(query, limit)` — BM25 full-text search; returns `(chunk_id, bm25_score)` ordered by relevance
- `search_scoped(query, source_prefixes, limit)` — BM25 search filtered to sources matching any prefix

### `ModelAvailabilityTracker` (`model_availability_tracker.py`)

Maintains aggregate routability state for RAG dependencies. Driven by Event Service WebSocket subscription (`model.available` / `model.unavailable`). HTTP seed provides initial snapshot. Subscribe loop reconnects automatically and resumes from last seen sequence number.

- `configure(model_ids)` — register tracked model IDs
- `refresh_snapshot()` — fetch current aggregate availability snapshot from Stargate
- `start_subscription()` — start background Event Service WebSocket subscriber task
- `wait_until_available(model_id, timeout_s)` — wait until model is routable or timeout
- `stop()` — cancel subscribe task and reset state

### `PropertyIndex` (`property_index/__init__.py`)

SQLite-backed property inverted index mapping property keys (`prop.{category}@@{value}`) to chunk IDs. Assembled from nine mixin classes (`_PropertyIndexPart01` through `_PropertyIndexPart09`) to satisfy SLOC limits. Write methods route through `SequentialExecutor`; reads access SQLite directly.

Capabilities span:
- Property key → chunk ID mappings with scope and source
- Indexed source freshness tracking (`indexed_sources`)
- Contextualization prefix cache (`contextualized_chunks`)
- Extraction queue management (`extraction_queue`)
- File-level indexing failure tracking (`indexing_failures`)
- Article metadata (`articles`)
- Corpus hints and scope vocabulary
- Contextualization exceptions
- FTS5 index attachment via `FtsIndex`

### `WatcherManager` (`watcher_manager/manager.py`)

Manages `HotReloadWatcher` instances for configured directories. Composed from `ScopeRepairMixin`, `ReconcileMixin`, `InitialReindexMixin`, `FileEventsMixin`, and `RegistrationMixin`. The file-system watcher fires for changes after it starts; files absent from the index are recovered by a periodic reconciliation sweep.

- `start(config)` — start watchers for all configured directories
- `stop()` — stop all watchers, background reindexes, and the reconciliation loop
- `register_directory(watch_directory)` — add a new watch directory at runtime
- `wait_for_initial_indexing(timeout)` — block until all background initial reindex tasks complete
- `request_reindex(file_path)` — schedule one file for reindex if watchers are live
- `get_status()` — return watcher status for diagnostics endpoints

---

## Key Functions

### Indexing Pipeline

- `_index_file(file_path, ...)` (`rag_service/indexing/index_file.py`) — index a file under a per-source gate to avoid watcher/API races
- `_index_file_impl(file_path, ...)` — inner implementation; extraction is decoupled: after successful ChromaDB upsert, the source is enqueued for async extraction
- `_run_embed_phase(...)` (`rag_service/indexing/embed.py`) — build chunk vectors, upsert to Chroma, write FTS entries
- `_run_contextualization_phase(...)` (`rag_service/indexing/contextualize.py`) — run contextualization phase; mutates metadatas in-place; returns embed texts and cache rows
- `_run_commit_phase(...)` (`rag_service/indexing/commit.py`) — stale cleanup, property writes, hints
- `_finalize_index_success(...)` (`rag_service/indexing/finalize.py`) — clear failure row, enqueue extraction, emit `rag.file.indexed`, return result
- `_delete_file(file_path)` (`rag_service/indexing/delete.py`) — delete all indexed chunks for a removed file under per-source gate

### Chunking

- `chunk_file(path, ...)` (`chunkers/office_dispatch.py`) — dispatch to the correct chunker based on file extension
- `chunk_markdown(path, content, ...)` (`chunkers/markdown_pdf.py`) — split markdown by headers, then paragraph-split within each section
- `chunk_pdf(path, ...)` — convert PDF to markdown via pymupdf4llm, normalize headings, then chunk
- `chunk_html(path, ...)` (`chunkers/epub_html.py`) — convert HTML to markdown, then chunk as markdown
- `chunk_epub(path, ...)` — extract EPUB chapters via ebooklib, normalize to markdown, then chunk
- `chunk_code_ast(path, content, ...)` (`chunkers/code_chunking.py`) — AST-aware Python chunker using tree-sitter (cAST split-merge algorithm)
- `chunk_code(path, content, ...)` — code chunker: AST-aware for Python, line-based fallback for others

### Contextualization

- `contextualize_chunks(chunks, source, model, ...)` (`contextualize.py`) — generate context prefixes for chunks via the rag-contextualize pipeline; returns `ContextualizationResult`
- `build_context_cache_plan(...)` (`contextualize_cache.py`) — build a reuse plan by matching chunk_hash metadata against cached prefixes
- `merge_computed_contexts(...)` — return a new contexts list with recomputed prefixes merged into miss positions
- `build_stored_context_rows(...)` — build persistence rows for non-empty computed prefixes

### Embedding

- `embed_chunks(texts)` (`embeddings/chunk_embed.py`) — embed raw texts for indexing with count- and token-bounded sub-batches
- `embed_query(text, scope)` (`embeddings/query_embed.py`) — embed a search query with bounded jittered backoff on transient errors
- `embed_queries_batch(texts, scope)` — embed multiple search queries in a single batch forward pass
- `post_embeddings(batch)` (`embeddings/batch_post.py`) — POST a single batch to the embedding endpoint with retry and fallback
- `wait_until_healthy(...)` (`embeddings/health.py`) — wait for aggregate embedding admission, then seed the cached embedding dimension

### Extraction

- `run_extraction_worker(...)` (`extraction/worker_loop.py`) — main extraction worker loop; runs until shutdown_event is set
- `extract_source(source, ...)` (`extraction/chroma_source.py`) — extract one source; returns `(all_done, increment_attempt, category, error, error_type)`
- `submit_extraction_pipeline(chunk_ids, chunk_texts, config)` (`knowledge_extractor.py`) — submit extraction to the async dispatch endpoint and return `execution_id`
- `poll_extraction_result(execution_id, chunk_ids)` — poll until execution reaches a terminal state and parse the output
- `cancel_extraction_execution(execution_id)` — cancel an in-flight Stargate extraction execution; best-effort
- `build_property_entries(knowledge, chunk_id, scope, source)` (`extraction/property_entries.py`) — build `(key, chunk_id, scope, source)` quads from extracted knowledge

### Search

- `execute_search(request)` (`rag_service/search.py`) — execute hybrid retrieval over indexed chunks and return ranked search results
- `apply_bm25_sidecar(...)` (`search_scope/bm25_sidecar.py`) — merge BM25 results into the dense candidate set via mini-RRF
- `apply_property_boost(...)` (`search_scope/property_boost.py`) — apply distance boost to chunks that match property index entries
- `apply_recency_sort(...)` (`search_scope/recency.py`) — reorder results by recency-adjusted score while preserving raw distances
- `apply_tier_weight(...)` (`tier_weighting.py`) — apply provenance-tier distance multipliers to retrieved chunks
- `apply_metadata_boost(...)` (`metadata_boost.py`) — apply metadata boost to RRF-merged chunks
- `resolve_scope_request(request, config)` (`search_scope/scope_resolution.py`) — resolve named scope(s) to merged source prefixes; reject conflicting fields

### Corpus Hints and Vocabulary

- `update_corpus_hints(property_index, ...)` (`corpus_hints/update.py`) — persist discriminative scope hints to metadata SQLite tables
- `detect_stale_scopes(...)` (`corpus_hints/freshness.py`) — return scope names where the current file-list hash differs from stored
- `run_scope_freshness_repair(...)` (`vocabulary/_repair.py`) — refresh corpus hints and vocabulary for scopes whose file-set hash drifted
- `classify_scope_async(...)` (`vocabulary/_classify.py`) — classify terms via Stargate chat completions (async)
- `filter_hints_by_cooccurrence(...)` (`corpus_hints/cooccurrence.py`) — return hint terms that co-occur with query terms at chunk level

### Directory Operations

- `collect_directory_candidates(...)` (`directory_ops.py`) — return candidate files and optionally the full walked source set
- `index_directory_contents(...)` — index files in a directory with concurrency control
- `find_sources_under_prefixes(...)` — return source paths under given prefixes from Chroma and metadata-only tables
- `purge_orphaned_sources(...)` — delete missing watched sources from Chroma and metadata-bearing storage
- `delete_sources(...)` — delete a set of sources consistently across Chroma and SQLite metadata

### Lifecycle

- `_startup()` (`rag_service/lifecycle.py`) — initialize local runtime state, then activate Stargate-backed dependencies asynchronously
- `_shutdown()` — shutdown RAG resources, cancel lifecycle tasks, and stop background services
- `register_admin_routes(...)` (`admin_routes/__init__.py`) — register all admin routes onto a new router using closures for shared state

### Entity Merging

- `merge_entities(entities)` (`entity_merging.py`) — merge entities with the same name across chunks; case-insensitive; unions types and facets
- `merge_relations(entities)` — merge relations across chunks; deduplicates by `(subject, predicate, target)`
- `merge_topics(topics)` — merge topics across chunks by name (case-insensitive), tracking frequency
- `format_entity_context(merged)` / `format_relation_context(merged)` / `format_topic_context(merged)` — format merged structures as context sections for prompt injection

### Noise Filtering

- `noise_reason(content, threshold)` (`chunk_filters.py`) — return a noise category string, or None if the chunk is not treated as noise
- `chunk_is_noise(content, threshold)` — True when `noise_reason` is not None
- `normalize_noise_metadata(metadata)` — align legacy bibliography flags with `is_noise` / `noise_reason` before upsert

### Failure Classification

- `classify_indexing_failure(exc, chunk_count)` (`indexing_failure_classifier.py`) — classify an indexing exception as permanent vs transient
- `classify_http_status_error(exc)` — classify an HTTP status error for indexing failure persistence

---

## Key Classes (DTOs and Protocols)

### Request/Response Models (`models/`)

| Class | Description |
|---|---|
| `SearchRequest` | Request body for RAG `/search`; scope and source_prefixes are mutually exclusive |
| `SearchResponse` | Search response |
| `StatsResponse` | Chunk/collection count for `/stats` |
| `ScopeInfo` / `ScopesResponse` | Named scope listing with prefixes and descriptions |
| `IndexRequest` / `IndexResult` | File indexing request and result |
| `DeleteResult` | Deletion result |
| `IndexDirectoryRequest` / `IndexDirectoryResponse` | Directory indexing request and response |
| `IndexingStatusResponse` | Unified indexing health payload for operator-facing status clients |
| `IndexingFailureResponse` | File-level indexing failure row exposed via the admin API |
| `IndexingFailuresListResponse` | Paginated indexing-failure listing |
| `DeleteIndexingFailureResponse` / `RetryIndexingFailureResponse` | Admin mutations on indexing failure rows |
| `ClearResponse` | Collection clear result |
| `SourceResponse` / `SourcesResponse` | Single-source chunk listing / source path listing |
| `ArticleUpsertRequest` / `ArticleUpsertResponse` | Article metadata upsert |
| `ArticleListingItem` / `ArticleListingResponse` | Article metadata listing |
| `SourceStatusItem` / `SourceStatusResponse` | Pipeline state for source files |
| `CoverageResponse` | Per-scope, per-prefix indexed file counts and recency |
| `ExtractionExportItem` / `ExtractionExportResponse` | Bulk extraction export |
| `ChunkIndexGroup` / `ChunkByIndexItem` | Chunk admin: source + index positions |
| `ChunksByIndexRequest` / `ChunksByIndexResponse` | Batched chunk fetch by source + index |
| `FailedChunkItem` / `FailedExtractionResponse` | Failed extraction chunk listing |
| `SourceDeleteResponse` / `DirectoryDeleteResponse` | Source/directory deletion results |
| `ExtractionQueueBreakdownModel` / `ExtractionQueueResponse` | Extraction queue state |
| `EmbedBatchRequest` / `EmbedBatchResponse` | Batch embedding request and result |
| `RerankRequest` / `RerankResponse` | Cross-encoder reranking |
| `WatcherStatusItem` | Single watcher state row |

### Protocols

| Class | Description |
|---|---|
| `IndexFileFn` (`directory_ops.py`) | Callable protocol for indexing a single file; `force=True` bypasses hash-unchanged check |
| `IndexFn` (`watcher_manager/protocols.py`) | Async callable protocol for watcher-driven indexing |
| `IndexOutcome` | Protocol for the outcome of an indexing operation on a single file |
| `DeleteOutcome` | Protocol for the outcome of a deletion operation on a single file |

---

## Imports and Dependencies

### External Libraries

- `fastapi` — HTTP framework; `APIRouter`, `FastAPI`, `HTTPException`, `Query`
- `pydantic` — `BaseModel`, `Field`, `model_validator`
- `chromadb` — vector store client; `PersistentClient`, `Collection`, `create_batches`
- `httpx` — async HTTP client
- `aiohttp` — async HTTP/WebSocket client
- `sqlite3` — SQLite database access
- `tree_sitter`, `tree_sitter_python` — AST parsing for code chunking
- `bs4` (`BeautifulSoup`) — HTML parsing
- `markdownify` — HTML to markdown conversion
- `yaml` — YAML config parsing

### Internal Shared Libraries

- `universal_event_bus` — `EventBus`, `Event`, `event_factory`, `MinimalEventDebugBroadcaster`, `SequentialExecutor`
- `universal_concurrency` — `FifoCapacityGate`
- `universal_hot_reload` — `HotReloadWatcher`, `matches_watch_exclude`
- `universal_logging` — `get_logger`
- `transport_utils` — `DEFAULT_STARGATE_URL`, `DEFAULT_CORTEX_URL`, `EVENTS_QUERY_SOCK`, `EVENTS_SUBSCRIBE_PATH`, `make_async_client`
- `model_id` — `ModelId`
- `markdown_sections` — `parse_sections`

### Internal Service Dependencies

- `services/rag/config` — `RagConfig`, `WatchDirectory`, `KnowledgeExtractionConfig`, `ScopeDefinition`, `load_config`, `save_scope`
- `services/rag/property_index` — `PropertyIndex` and associated data classes
- `services/rag/embeddings` — embedding client functions and runtime state
- `services/rag/chunkers` — `chunk_file`, `Chunk`, `normalize_html_to_markdown`
- `services/rag/events/*` — typed event factories for all RAG lifecycle domains
- `services/rag/models` — all request/response DTOs
- `services/rag/fts_index` — `FtsIndex`
- `services/rag/admission_gate` — `AdmissionGate`
- `services/rag/entity_admission` — `EntityAdmissionGate`
- `services/rag/extraction_admission` — `ExtractionAdmissionGate`
- `services/rag/knowledge_extractor` — extraction pipeline client
- `services/rag/entity_merging` — entity/relation/topic merge functions
- `services/rag/corpus_hints` — corpus hint generation and loading
- `services/rag/vocabulary` — vocabulary classification and repair
- `services/rag/search_scope` — hybrid search post-processing
- `services/rag/tier_weighting` — provenance-tier distance weighting
- `services/rag/metadata_boost` — post-RRF metadata boost
- `services/rag/directory_ops` — directory-level source discovery and cleanup
- `services/rag/article_registry` — article registry helpers
- `services/rag/chunk_filters` — noise detection
- `services/rag/indexing_failure_classifier` — failure classification
- `services/rag/indexing_helpers` — Chroma source migration and file hashing
- `services/rag/model_availability_tracker` — model availability tracking
- `services/rag/watcher_manager` — `WatcherManager`
- `services/rag/rag_service/state` — shared mutable service-level objects
- `services/rag/rag_service/indexing` — indexing pipeline
- `services/rag/extraction` — extraction worker helpers

---

## Open Human Synthesis Markers

<!-- AUTHORED -->
### Indexing Data Flow and Per-Source Serialization

Watcher events, admin `/index` routes, and reconciliation sweeps all converge on `_index_file` in `rag_service/indexing/index_file.py`. Before any work begins, the call acquires a **per-source FIFO gate** (`source_path_gate.py`): only one index or delete operation may run against a given absolute file path at a time. This prevents races when a hot-reload watcher fires while an operator-triggered reindex is in flight on the same source.

For **entity-gated** watch roots, `EntityAdmissionGate` enforces a second invariant: only paths backed by a cortex entity `source_uri` may index. The gate defaults EMPTY (fail-closed). Enforcement spans two layers, both emitting `rag.file.indexing.gated` (coordination — not an indexing failure row):

- **Layer 1 (`watcher_sweep`)** — `WatcherManager._should_attempt` consults `is_admitted` before watcher, reconcile, and initial-sweep dispatch; non-admitted files skip indexing at the sweep layer.
- **Layer 2 (`index_funnel`)** — `_index_file_impl` re-checks admission for every entry path (inotify, admin reindex, etc.); non-admitted files return unchanged with `layer="index_funnel"`.

Sweeps short-circuit at Layer 1, so a sweep-skipped file never reaches Layer 2 — at most one gated signal per source per sweep, no double emission.

Inside the gate, `_index_file_impl` runs the indexing funnel: chunk → contextualize (optional, admission-gated) → embed → Chroma upsert + FTS5 write → commit (property index, hints freshness) → finalize. **Extraction is decoupled**: after a successful Chroma commit, `_finalize_index_success` enqueues the source on the SQLite extraction queue; the async extraction worker drains that queue independently. Watcher and admin callers therefore return as soon as vectors are searchable — extraction enrichment proceeds in the background.

<!-- AUTHORED -->
### Pool A vs Pool B at Query Time

**Pool A (dense + sparse hybrid)** runs inside the RAG service `/search` endpoint: ChromaDB cosine similarity on precomputed query embeddings, merged with a BM25 sidecar via mini-RRF in `search_scope/bm25_sidecar.py`. It finds semantically related chunks even when query vocabulary differs from corpus text.

**Pool B (vocabulary-aware sparse)** is orchestrated by the pipeline layer (`rag-context`, `rag-answer`): FTS5 BM25 queries built from phrase factoring and corpus-hint IDF expansion, dispatched with `sparse_only=True` so no embedding model is involved. Pool B searches the full corpus independently — it does not re-score Pool A hits.

At merge time the pipeline applies reciprocal rank fusion across pools, then metadata boost, source habituation, and optional Pool-B-source swap. The RAG service exposes the low-level `/search` primitive; the two-pool architecture lives in the pipeline handlers that call it.

**Scope boundary:** Pool B orchestration (phrase factoring, IDF expansion, cross-pool RRF, source habituation, Pool-B-source swap) is pipeline-layer behavior outside this doc's `services/rag` scope. The service primitive is `/search` with `sparse_only=True`. End-to-end Pool A/B description: `services/rag/README.md` (§ Retrieval: Two-Pool Architecture).

<!-- AUTHORED -->
### Stargate Pipeline Registration

Contextualization and knowledge extraction are **Stargate-registered pipelines**, not inline LLM calls. `contextualize.py` submits to the `rag-contextualize` pipeline; `knowledge_extractor.py` submits to `rag-extraction` and polls for completion. Pipeline YAML lives under `pipelines/rag_contextualize/` and `pipelines/rag_extraction/`; Stargate loads them at startup and exposes them as virtual model IDs on `/v1/chat/completions`.

The RAG service discovers pipeline availability through the same Stargate `/v1/models` catalog used for embedding models. Index-time submissions coordinate capacity through `AdmissionGate` (contextualize) and `ExtractionAdmissionGate` (extraction worker), subscribing to Stargate coordination signals rather than using hand-rolled concurrency caps.

<!-- GENERATED:END -->