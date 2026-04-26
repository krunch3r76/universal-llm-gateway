# RAG Service

> **Documentation status**: This is a capability overview. Comprehensive API reference and configuration guide are pending.

A semantic search and knowledge management service backed by ChromaDB. Runs as a FastAPI application communicating over Unix domain socket (default: `/tmp/universal-protocol/rag.sock`, overrideable via `RAG_SOCKET_PATH`) or TCP.

## Design Philosophy: Index Smart, Search Cheap

The standard RAG playbook is _index cheap, search smart_ — chunk, embed, and
compensate for shallow indexing with expensive query-time mechanisms (MMR for
diversity, LLM rewriting for vocabulary gaps, heavier rerankers for precision).

This architecture inverts that: **index smart, search cheap**. Heavy analytical
work happens once per document at index time and is amortized across every
query. At search time, diversity is a score multiplier (counter lookup), exact
matches come from vocabulary-aware full-text search, and expansion uses
precomputed corpus statistics — no per-query LLM calls, no pairwise embedding
comparisons for diversity, no MMR.

The open question being tested: once vocabulary expansion and corpus hints are
steering sparse retrieval effectively, how much does the dense embedding signal
actually add? Early evidence suggests the index-time investment has narrowed
that gap enough to ask seriously.

There is a known limitation: IDF expansion (Inverse Document Frequency — a score that ranks terms by how selectively they appear across documents: rare terms score high, common terms score low) is bounded by the property index
vocabulary. If the query uses a synonym never extracted as an entity or topic
in any chunk, expansion returns nothing and Pool A's dense signal is the only
bridge.

The practical frequency of this gap depends on extraction model quality and corpus vocabulary coverage. Two extraction-pipeline steps determine that quality:

- **Knowledge extraction** — `qwen3-14b` (local, see `pipelines/rag_extraction/models.yaml`). Pulls entities, topics, and relations per chunk into the property index.
- **Vocabulary classification** — post-processes the property index via `classify_vocabulary.py`. Default mode uses whatever gateway model is loaded; `--mode frontier` routes to cloud/frontier models. A frontier model running over `qwen3-14b`'s output can compensate for gaps — surfacing discriminative register terms the local model underweighted and enriching the vocabulary the expansion can draw from.

All current retrieval observations are against `qwen3-14b` extraction + local vocabulary classification. The weaker-model degradation path and the frontier-classification compensation effect are both untested.

## Architecture

### Index Time — the Front-Loaded Investment

1. **Chunking** — files are split into semantically coherent chunks using target+pad sizing with paragraph overlap and heading injection. Code files use tree-sitter AST-based chunking. PDF inputs are converted to markdown first; dominant `pymupdf4llm` bold-heading patterns are normalized into ATX headings before section parsing, but a small residual class of inline bold subheadings may still remain.
2. **Source hashing** — plain SHA-256 of file bytes is stored as `source_hash` on every chunk (PDF, Markdown, HTML, etc.). This hash serves as the universal join key to the `articles` table for query-time metadata enrichment.
3. **Knowledge extraction** — the `rag-extraction` LLM pipeline extracts entities, types, facets, topics, and relations from each chunk. Results are stored in both ChromaDB metadata and a SQLite-backed property inverted index. This is the most expensive index-time step, but it enables deterministic metadata boost and IDF expansion at search time.
4. **Full-text indexing (FTS5)** — every chunk's text is indexed in a SQLite FTS5 full-text index alongside the vector store. This powers Pool B sparse retrieval with BM25 scoring — no embedding model involved. Lives in the same `rag_metadata.db` as the property index.
5. **Contextualization** — on by default (omit `contextualize_model` or set it to a model ID). Per-chunk LLM-generated context prefixes are prepended only for embedding; stored document text stays unchanged. Set `contextualize_model: ""` to disable. Improves retrieval when chunks share overlapping vocabulary. Index-time LLM submissions coordinate with Stargate through `AdmissionGate` — see [Coordination: Event-Driven Admission Gate](#coordination-event-driven-admission-gate).
6. **Embedding** — chunks (with context prefix when contextualization ran) are embedded via the configured local embedding model (default: `qwen3-embedding-8b`) through the Gateway and stored in ChromaDB with cosine similarity.
7. **Pending journal** — tracks in-flight indexing operations. On restart, interrupted files are re-indexed before the watcher starts, eliminating dangling pointers.

### Post-Index Enrichment — Corpus-Level Analysis

After bulk indexing, three derived artifacts are built from the property index:

- **Corpus hints** (`corpus_hints.py`) — aggregates terms from the property index per scope, computing co-occurrence statistics. At search time, these power IDF-weighted corpus expansion: for any query term, the system knows which other terms frequently appear alongside it in the corpus. This replaces LLM-based query expansion with deterministic corpus statistics.
- **Scope vocabulary** (`scripts/rag/classify_vocabulary.py`) — LLM-classifies corpus hint terms into configurable taxonomy categories (see `vocabulary_taxonomy` in rag.yaml). Classification serves two purposes: the result is injected into generation prompts so the model understands what kind of language the corpus speaks, and category order determines retrieval anchor priority — specification terms are anchored first because named standards are the most selective signals; academic terms are lowest priority because they are often too broad to anchor a specific result.
- **Noise classification** (`scripts/rag/classify_noise.py`) — LLM-classifies chunks to tag bibliographies, boilerplate, and non-intelligible content for filtering.

### Search Time — Two-Pool Retrieval

The RAG service exposes low-level search primitives (`/search`, `/embed_batch`). The pipeline layer orchestrates these into a two-pool architecture:

8. **Pool A — broad semantic retrieval** — ChromaDB cosine similarity + BM25 sidecar, merged via mini-RRF. Finds text related to the _idea_ even when query vocabulary differs from corpus text. Standard dense+sparse hybrid.
9. **Pool B — vocabulary-aware sparse retrieval** — FTS5 full-text search with BM25 ranking, no embedding model. Queries are factored into sub-phrases (phrase extraction) and augmented with corpus-derived co-occurrence terms (IDF expansion from corpus hints). Each facet is dispatched as its own keyword query, catching identifiers and technical terms that dense search blurs. Pool B searches the full corpus independently — it doesn't re-score Pool A's results.
10. **RRF merge** — Pool A and Pool B results are merged via reciprocal rank fusion. Rank position is the only signal; cosine distances from different queries are not comparable.
11. **Metadata boost** — entity/topic/relation overlap between query and chunk metadata adjusts scores post-RRF. Deterministic — no LLM calls.
12. **Source habituation** — graduated diversity scoring that tracks _where_ results come from, not content similarity:
    - _Lateral_ (Pool B): first hit per source gets a boost; subsequent hits from the same source are penalized.
    - _Global_ (all pools): each additional chunk from an already-represented source is penalized exponentially. A counter lookup, not a pairwise comparison.
13. **Pool B source swap** — when a source has Pool B hits, redundant Pool A chunks from the same source are candidates for eviction. Binary mode (default): evict all. Graduated mode: retain Pool A chunks whose embedding distance from Pool B hits exceeds a threshold.
14. **Article enrichment** — unique `source_hash` values from the result set are batch-looked up in the `articles` table. Matching article metadata (`article_title`, `article_authors`, `article_venue`, `article_published_date`, `article_doi`) is merged into each chunk's metadata dict.
15. **Recency scoring** — additive recency weight based on `published_date` (preferred for research papers) or `indexed_at` timestamps. Naive ISO timestamps are normalized to UTC before scoring to avoid timezone subtraction errors.

The pipeline layer (`rag-context`, `rag-answer`, `rag-answer-deep`) orchestrates retrieval, reranking, and answer generation. See [Pipeline Layer](#pipeline-layer-rag-context) below.

## Pipeline Layer (`rag-context`)

The `rag-context` pipeline (v2.0) runs on top of the RAG service and implements the two-pool retrieval architecture with Gateway-managed reranking. Zero LLM calls on the default path. Exposed as a virtual model ID (`rag-context`, `rag-answer`, `rag-answer-deep`) through Stargate. Average end-to-end latency: **~1.6s**.

### Pipeline Steps

| Step | Handler | LLM? | Description |
|------|---------|-------|-------------|
| `direct_scope` | `rag_direct_scope_v1` | No | Fixed scope resolution (default `"all"`, overridable via `scope_override`) |
| `generate_hyde` | `generate` | Yes (conditional) | Hypothetical document embedding — only runs when `hyde_enabled: true` (default: off) |
| `retrieve_assemble` | `rag_multi_retrieve_v1` | No | Batched embedding + concurrent IDF expansion + two-pool retrieval + RRF merge + source habituation |
| `rerank_assemble` | `rag_rerank_assemble_v1` | Depends on mode | Gateway-managed reranking (default, ~100ms) or generative sliding-window reranker |

### Pre-Retrieval: Inline Term Expansion

Query factoring and IDF expansion run concurrently with dense retrieval (zero added latency). Pure functions in `term_expansion.py`:

1. **Phrase extraction** — decomposes the query into sub-phrases for independent sparse-only BM25 queries (Pool B)
2. **IDF-weighted corpus expansion** — queries the property index for terms that co-occur with the query's most discriminative words, surfacing vocabulary the user didn't use but the corpus contains. Uses corpus hints built at index time — deterministic corpus statistics, not LLM guessing.

These run via `asyncio.to_thread` alongside the pool A embedding calls, eliminating the ~300ms serial bottleneck of the old `expand_terms` pipeline step.

### Retrieval: Two-Pool Architecture with Batched Embedding

All query embeddings are computed in a **single batched GPU pass** via the `/embed_batch` endpoint before dispatch, then passed as pre-computed vectors to individual `/search` calls. This eliminates sequential per-query GPU embedding passes.

**Pool A — Dense + Sparse hybrid**: ChromaDB cosine similarity + BM25 sidecar, one query per original + HyDE variant, merged via RRF. Uses pre-computed embeddings. Standard retrieval — finds text related to the _idea_ even when the query uses different words.

**Pool B — Vocabulary-aware sparse retrieval**: For each facet from inline IDF expansion, constructs an OR-joined FTS5 query and dispatches with `sparse_only=True` (bypasses embedding entirely). Pool B searches the full corpus independently — it doesn't re-score Pool A's results. Exact-match chunks enter the merged list with scores reflecting their match strength, evaluated on their own terms rather than competing against twenty fuzzy results in a single ranked list.

### Post-Merge Scoring: Source Habituation

Source habituation is the diversity mechanism. It targets _where_ results come from (source identity), not what they contain (content similarity). This is a fundamentally different axis from MMR:

- **Lateral source habituation (Pool B)**: the highest-scoring chunk from a given source gets the full `facet_pool_score_boost` (default 1.5×); subsequent chunks from the same source are penalized (÷boost). Prevents any single source from monopolizing boosted Pool B slots. **Tradeoff**: a second chunk from the same source may be richer than the first — the system accepts this risk in exchange for source breadth. Callers that need depth from a single authoritative source should set `facet_pool_score_boost: 1.0` or use `retrieval_path: "general"`.
- **Global source habituation**: after sorting, each additional chunk from an already-represented source is penalized exponentially (`score /= factor^n`, n ≥ 1). Applies to both pools. A counter lookup — no embedding comparisons, no dot products.
- **Pool B source swap**: when a source has Pool B hits, Pool A chunks from that source are candidates for eviction. Binary mode (default, `facet_pool_swap_distance_threshold=0.0`): evict all. Graduated mode (threshold > 0): retain up to `facet_pool_swap_max_retain` Pool A chunks whose cosine distance from every Pool B hit exceeds the threshold.

The Pool B source swap and corpus expansion are in productive tension: expansion reliably surfaces the same prominent sources Pool A finds via dense similarity — overlap is the expected steady state, not an edge case. Eviction is the routine consequence: Pool A's representation of those sources is replaced by Pool B's, which arrived via more precise vocabulary. The value of expansion is what it produces beyond the overlap — sources Pool A never found at all fill the freed slots.

For research papers (focused, single-topic documents), binary eviction is safe: when both pools converge on the same paper, they land on the same core content. Graduated mode rarely changes the outcome. For long multi-topic documents (design specs, architecture files), both pools converge on the same file but via different sections — graduated mode retains Pool A chunks covering sections Pool B didn't reach. For project and architecture documentation — where queries often have a single authoritative source and depth matters more than breadth — the swap is disabled entirely via `retrieval_path: "general"`. Tuning the swap threshold is not the right lever for those corpora; disabling the whole diversity layer is.

> **Observation status**: The above characterization of overlap frequency, binary vs graduated outcomes, and expansion as the primary diversity mechanism is from pipeline trace analysis, not formal evaluation. See thread 156 (agent-bus) for the planned post-reindex quality comparison.

### Two Retrieval Paths: Research and General

Not every query benefits from source diversity. Research queries want breadth — evidence from multiple independent papers. Project and documentation queries want depth — the single best document may contain the entire answer, and penalizing it to surface a tangentially related file is counterproductive.

The `retrieval_path` pipeline option selects a named diversity preset. The caller chooses the path; the pipeline applies it before any other tunable resolution.

| `retrieval_path` | `source_diversity_max` | `source_habituation_factor` | `facet_pool_swap_enabled` | Behavior |
|-------------------|------------------------|-----------------------------|---------------------------|----------|
| `research` (default) | 3 | 1.5 | true | Aggressive diversity — caps per-source chunks, penalizes repetition, swaps redundant Pool A hits |
| `general` | 0 (disabled) | 1.0 (disabled) | false | Pure relevance — no per-source cap, no habituation penalty, no swap eviction |

The two-pool architecture, vocabulary expansion, and metadata boost apply identically on both paths. Only the post-merge diversity layer changes. Individual diversity keys (`source_diversity_max`, `source_habituation_factor`, `facet_pool_swap_enabled`) can still be overridden per request via `pipeline_options` — the path is a named preset, not a lock.

Example: `project-assistant` forwards `retrieval_path: "general"` to `rag-context` so project documentation queries get depth-first ranking.

### Reranking: Refine, Don't Bulldoze

Default mode: **gateway reranker**. A managed reranker in Gateway scores (query, passage) pairs in a single forward pass — ~80-175ms for 14 passages on GPU. Scores are fused with RRF prior: `final = prior_weight × rrf_score + (1 − prior_weight) × reranker_score`, bounded by `rerank_max_movement`.

The 0.70/0.30 default fusion keeps the retrieval signal dominant. A **movement cap** (default 3 positions) prevents a marginally relevant chunk from leapfrogging a stronger retrieval result regardless of reranker score.

Alternative mode: **generative** (`rerank_mode: generative`). Sliding-window LLM reranker with facet-aware prompting. Higher quality ceiling but 4-7s latency.

Reranker model lifecycle is managed by Gateway; RAG no longer hosts a local reranker runtime.

### Key Pipeline Options

| Option | Default | Effect |
|--------|---------|--------|
| `retrieval_path` | `research` | `general` for depth-first ranking (no diversity penalties). Named preset — sets `source_diversity_max`, `source_habituation_factor`, `facet_pool_swap_enabled`. |
| `rerank_mode` | `gateway` (literal) | `generative` for LLM sliding-window reranker |
| `rerank_enabled` | `true` | `false` skips reranking entirely |
| `hyde_enabled` | `false` | `true` adds one LLM call for hypothetical document embedding |
| `max_idf_terms` | `8` | `0` skips IDF expansion |
| `max_discriminative` | `4` | Number of query words used as IDF seeds |
| `rerank_max_candidates` | `14` | Passages sent to cross-encoder |
| `rerank_prior_weight` | `0.70` | Weight of RRF score vs reranker score |
| `rerank_max_movement` | `3` | Max rank positions a chunk can shift during reranking |
| `source_diversity_max` | `3` | Max chunks per source post-RRF; `0` disables. Overrides `retrieval_path` preset. Set `0` when a single authoritative source should contribute as many chunks as relevance warrants. |
| `source_habituation_factor` | `1.5` | Exponential penalty per additional same-source chunk; `1.0` disables. Overrides preset. Set `1.0` when depth from one source matters more than breadth across sources. |
| `facet_pool_swap_enabled` | `true` | Pool B source swap; `false` disables eviction. Overrides preset. |

### Pipeline IDs

| Model ID | Description |
|----------|-------------|
| `rag-context` | Returns assembled context chunks (no answer generation). Default for MCP `rag(op="search")`. |
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
| PDF (`.pdf`) | Native text extraction via `pymupdf4llm`, then heading normalization plus section-aware markdown chunking. Dominant bold-heading patterns convert cleanly; a small residual class of inline bold subheadings may persist. |
| EPUB / Ebook | Text extraction and structural chunking |
| HTML (`.html`, `.htm`) | BeautifulSoup + markdownify normalization |
| Plain text (`.txt`) | Paragraph-based chunking |

## API Endpoints

### Search

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /search` | POST | Semantic search — accepts `query`, `top_k`, `scope`, `source_prefixes`, `recency_weight`, `max_distance`, optional `query_embedding` (pre-computed) |
| `POST /embed_batch` | POST | Batch-embed multiple query texts in a single GPU forward pass. Returns list of embedding vectors. |
| `POST /chunks_by_index` | POST | Fetch chunks by source path and chunk index (neighbor expansion) |

### Indexing

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /index` | POST | Index a single file |
| `POST /reindex` | POST | Reindex a single file (re-chunk, re-embed, re-extract), even when unchanged on disk |
| `POST /index_directory` | POST | Index all supported files in a directory |
| `POST /reindex_directory` | POST | Reindex directory — removes stale sources and reruns walked files; `force=true` clears directory state first |
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
    exclude: ["trading/**", "CORPUS_MANIFEST.md"]

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
contextualize_request_timeout_s: 300  # server-side budget for each context LLM call
contextualize_client_timeout_s: 600   # outer RAG HTTP ceiling; must exceed request timeout
contextualize_tail_idle_timeout_s: 45 # abandon stragglers after progress stalls
contextualize_tail_min_success_ratio: 0.5 # tail policy starts only after enough successes

# Vocabulary taxonomy: ordered list of classification categories.
# Order determines retrieval anchor priority — index 0 = highest priority.
# Add a new category here when adding a corpus domain with distinct vocabulary.
# Re-classify the affected scope(s) after editing (takes minutes, no reindex needed).
vocabulary_taxonomy:
  - specification   # named standards/protocols — most selective anchors
  - practitioner    # tools, libraries, workflows
  - academic        # theoretical concepts, named models
  # - quantitative  # example: add for trading/finance corpus
```

### Key Options

| Option | Purpose |
|--------|---------|
| `watch_directories` | Directories to watch with inotify; auto-reindex on file changes. `exclude` globs are matched against watch-root-relative paths (e.g. `trading/**`) and bare filename globs also match basenames |
| `scopes` | Named retrieval scopes — consumers reference by name; `union: true` aggregates all |
| `chunk_tokens` | Target chunk size per directory (default varies: 1024 for docs, 256 for code) |
| `knowledge_extraction` | LLM extraction config — pipeline, boost factor, retry limits |
| `post_index_enforcement` | `strict` (default): return 503 on search until post-index enrichment watermarks are current. `warn`: log ERROR at startup but continue serving |
| `contextualize_model` | Model ID for per-chunk context generation before embedding. Omit for default (on); set to `""` to disable |
| `contextualize_request_timeout_s` | Per-context LLM request budget sent to Stargate as `X-Request-Timeout`. Default 300s |
| `contextualize_client_timeout_s` | Outer RAG HTTP timeout covering queue/load plus request execution. Default 600s |
| `contextualize_tail_idle_timeout_s` | No-progress tail budget after enough chunks have succeeded. Default 45s; abandoned stragglers remain cache misses |
| `contextualize_tail_min_success_ratio` | Fraction of cache-miss chunks that must succeed before tail abandonment may fire. Default 0.5 |
| `reconcile_interval_s` | Base seconds between watcher reconcile sweeps. Default 300 (5 min). 0 = disabled. Reconcile uses the same worker pool as initial reindex; when a sweep recovers files, the next sweep runs after 30 s (busy interval), otherwise after the full interval |
| `vocabulary_taxonomy` | Ordered list of vocabulary categories for classification. Order = retrieval anchor priority. Extend to add domain-specific categories; re-classify affected scopes to take effect. Default: `[specification, practitioner, academic]` |

## Contextualize Cache

When enabled, RAG reuses previously computed contextualization prefixes for
unchanged chunks of unchanged source content. The cache is best-effort and
non-authoritative: lookup failures degrade to full recompute and store
failures do not fail indexing.

Cache keys: `(source_hash, chunk_hash, contextualize_model,
contextualize_schema_version)` where:

- `source_hash` = sha-256 of file bytes
- `chunk_hash` = short hash over chunk text **plus positional info** so
  identical text at different positions in the same source does not
  collide
- `contextualize_schema_version` = sha-256 over the prompt text, neighbor
  budgets (`_NEIGHBOR_CHARS`, `_MAX_CONTEXT_TOKENS`), and
  `inspect.getsource(_build_chunk_context)` — editing the assembly
  function invalidates the cache automatically

Invalidation triggers: file content change, prompt edit, model change, or
any edit to `_build_chunk_context` / relevant tunables. Empty prefixes
(per-chunk contextualize failure) are never persisted — the V10 CHECK
constraint backstops the application-layer filter.

Garbage collection: primary cleanup runs inside `remove_source_metadata`
(single-file or directory delete). A non-fatal startup sweep backstops
crashes between `contextualized_chunks` writes and `indexed_sources`
cleanup.

Operator visibility:

- `/indexing/status.contextualize_cache_rows` — row-count capacity view
- `rag.contextualize.cache.evaluated` — per-file hit/miss summary
- `rag.contextualize.cache.gc.completed` / `.failed` — startup sweep result
- `rag.contextualize.cache.store.completed` / `.failed` — best-effort
  post-upsert persistence outcome
- `rag.contextualize.cache.lookup.failed` — lookup degraded to full
  recompute

Partial contextualization is an exception path. RAG still indexes the file so
stragglers do not waste worker time, but every degraded attempt emits
`rag.contextualization.partial` and stores a durable row in
`contextualization_exceptions` with failed/abandoned chunk counts, abandoned
chunk indices, request IDs, timing, model, operation ID, and first failure.

Note: after the cache ships, `rag.contextualization.started/.completed`
`chunk_count` reports **cache misses only** (actual LLM work). Use
`rag.contextualize.cache.evaluated.total_chunks` for the full file total.

## Coordination: Event-Driven Admission Gate

Contextualization sends one LLM request per chunk per file. RAG no longer caps
global in-flight contextualization locally; Stargate owns model admission and
loads models on demand. During bulk indexing, RAG workers use `AdmissionGate`
as an advisory pause/resume surface so large batches do not keep submitting into
known cold-load or starvation-drain windows.

`AdmissionGate` subscribes to Stargate coordination signals:

- `capacity.admission.paused` / `capacity.admission.resumed` — close or open
  the gate when Stargate is draining capacity for a starved competing model.
- `model.loading.started` / `model.loaded` — close or open the gate during the
  contextualize model's cold-load window.
- `model.load.failed` — reopen the gate so the next request retries through
  Stargate and fails loudly if the model cannot load.
- `federation.gateway.degraded` / `federation.gateway.recovered` — close the
  gate while a federated gateway is timing out, then reopen only after recovery
  and after any model/capacity close reason has cleared.

The gate defaults OPEN. The first cold batch can still produce a bounded burst
before `model.loading.started` reaches RAG; subsequent batches wait on the
event-driven gate. Per-request `X-Request-Timeout` enforcement in Stargate is
the correctness backstop.

### Implementation

| File | Role |
|------|------|
| `services/rag/admission_gate.py` | Subscribes to Stargate coordination events and exposes `wait_for_admission()` |
| `services/rag/rag_service/state.py` | Stores `_admission_gate: AdmissionGate \| None` |
| `services/rag/rag_service/lifecycle.py` | Starts the gate when `contextualize_model` is set; stops it on shutdown |
| `services/rag/contextualize.py` | Workers wait for admission before each LLM call |
| `services/rag/rag_service/indexing.py` | Passes `state._admission_gate` to `contextualize_chunks()` |

### When to apply this pattern

Any RAG pipeline step that sends per-chunk LLM requests during bulk indexing is
a candidate for the same admission pattern: construct the shared `AdmissionGate`
at startup, thread it through indexing into the worker function, and call
`wait_for_admission()` before the LLM request. Do not reintroduce local global
concurrency caps or wall-clock backoff.

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

Indexing handles chunk extraction, embedding, knowledge extraction, and per-chunk `is_noise` / `noise_reason` tagging (heuristic; `normalize_noise_metadata` aligns legacy `is_bibliography` and fills missing `noise_reason` on upsert). Pipeline fixtures and snapshots may keep `is_bibliography: false` only — readers use `chunk_metadata_is_noise` which accepts both keys. After a large corpus refresh, three enrichment steps rebuild derived artifacts. The serving gate tracks corpus hints and scope vocabulary; LLM noise classification is an optional backfill because index-time heuristic noise tagging keeps search usable.

1. **Corpus hints** (`python -m services.rag.corpus_hints`) — aggregates terms from the property index per scope into the `corpus_hints` table in `rag_metadata.db`.
2. **Scope vocabulary** (`scripts/rag/classify_vocabulary.py`) — LLM-classifies corpus hint terms into taxonomy categories (configured by `vocabulary_taxonomy` in rag.yaml) and writes to the `scope_vocabulary` table in `rag_metadata.db`. Fail-closed: aborts without writing if any scope fails classification.
3. **Noise classification** (`scripts/rag/classify_noise.py`) — LLM-classifies chunks: `--target bibliography` writes `is_noise`; `non_intelligible` writes `is_non_intelligible`; `--target describe` writes `noise_description` for review. Resumable by metadata key.

Each step stamps a watermark in `rag_metadata.db`. When `post_index_enforcement: strict` (default), the service returns 503 on search requests until serving-critical watermarks (`corpus_hints`, `vocabulary`) are current relative to the last reindex. In `warn` mode, an ERROR is logged at startup but search continues.

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
| `scripts/rag/classify_vocabulary.py` | LLM-classify scope vocabulary into taxonomy categories from corpus hints |
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
| Metadata DB schema | `services/rag/property_index.py` | Schema versioning, migration, all metadata tables |
| Corpus hints flow | `services/rag/corpus_hints.py` | Hint generation, DB read/write, co-occurrence filtering |
| Vocabulary classification | `scripts/rag/classify_vocabulary.py` | LLM-based taxonomy classification (configurable categories) |
| Enrichment runbook | `tasks/runbooks/rag-post-index-refresh.md` | Operator post-index workflow |
| RAG config | `services/rag/config.py` | `RagConfig` dataclass, YAML parsing |
| MCP RAG tools | `services/mcp-server/tools/rag.py` | `rag(op="search")`, `rag(op="list_scopes")`, prefix passthrough |

## Known Gaps

### Source-depth retrieval — "give me more from this source"

Context blocks are attributed with source title, authors, and date, so an agent reading the injected context can identify which sources contributed. What it cannot do is request additional chunks from a specific source — there is no retrieval operation that accepts a source path or title and returns more from it.

The current workaround is a follow-up query, which relies on the same source surfacing again through normal retrieval. For an agent that has already identified a relevant source and needs depth (not breadth), this is wasteful.

The intended design: breadth-first retrieval across the corpus surfaces which sources are relevant; the agent then requests depth from the ones that matter. The attribution metadata already makes the first half possible. The retrieval interface needs to expose the second half — a source-scoped fetch that bypasses diversity mechanisms and returns as many chunks from the target source as relevance warrants. Tracked as `todo:rag-source-depth-retrieval`.

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
| `admin_routes.py` | Administrative API endpoints |

## Indexing Failures

File-level indexing failures are persisted to the `indexing_failures` table in
`~/.rag/store/rag_metadata.db` so the reconcile loop does not re-burn LLM
inference and embedding compute on files that cannot succeed.

### Classifier taxonomy

`services/rag/rag_service/indexing.py::_classify_indexing_failure` inspects the
raised exception and returns `(category, reason)`:

- **`permanent`** — the file will fail the same way until its content changes
  or operator config changes. Examples: `exceeds_chroma_max_batch_size`,
  `permission_denied`, `file_not_found`, `embedding_dimension_mismatch`,
  `unsupported_file_type`, `contextualize_model_not_in_catalog`.
- **`transient`** — retry may succeed. Examples: `timeout`,
  `contextualize_probe_failed`, `gateway_capacity`,
  `event_service_disconnected`, `unclassified`.

The `NOT_IN_CATALOG` / `PROBE_FAILED` split follows
`stargate-model-lifecycle_ws.mdc`.

### Reconcile behavior

Before dispatching a file, `WatcherManager._should_attempt(fp)` consults the
table:

1. No row → attempt.
2. Row exists but `mtime_ns` or `size_bytes` differs from the file on disk →
   content changed, attempt (classifier will record a fresh row if it fails
   the same way).
3. `permanent` row with unchanged mtime/size → emit
   `rag.file.indexing.failure.skipped`, skip.
4. `transient` row inside backoff window
   (`base = max(reconcile_interval_s, 60) s`, doubled per attempt up to 3600 s
   cap) → skip.
5. `transient` row outside backoff window → attempt.

### Admin API

All endpoints are served on the RAG UDS (`/tmp/universal-protocol/rag.sock`).
URL-encode slashes and special characters in source paths.

```bash
# List failures (category ∈ {all, permanent, transient})
curl --unix-socket /tmp/universal-protocol/rag.sock \
  "http://localhost/indexing_failures?category=permanent"

# Clear one row (operator override)
curl -X DELETE --unix-socket /tmp/universal-protocol/rag.sock \
  "http://localhost/indexing_failures/$(python -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1], safe=""))' "/abs/path/to/file.pdf")"

# Retry (clears row and enqueues a reindex)
curl -X POST --unix-socket /tmp/universal-protocol/rag.sock \
  "http://localhost/indexing_failures/$(python -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1], safe=""))' "/abs/path/to/file.pdf")/retry"
```

### Status counts

`GET /indexing/status` exposes `indexing_failures_permanent_count` and
`indexing_failures_transient_count` for operator dashboards
(`rag-status --watch`).

### Events

- `rag.file.indexing.failure.recorded` — row persisted.
- `rag.file.indexing.failure.skipped` — reconcile/initial sweep gated a file.
- `rag.file.indexing.failure.cleared` — row removed
  (`indexed_successfully` / `source_deleted` / `operator_cleared`).
- `rag.file.indexing.failure.retry.requested` — operator issued retry;
  `scheduled` indicates watcher admission.

All four are `role=coordination`.
