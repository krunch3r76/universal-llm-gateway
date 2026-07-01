<!-- target:* -->
# RAG Architecture

**RAG service architecture and patterns** (gateway domain).

## Source Hash Join Pattern

Indexing and article metadata are **decoupled**:

1. **Index time**: every chunk gets `source_hash = SHA-256(file_bytes)` — plain hash, no schema version suffix. Stored on all chunk types (PDF, MD, HTML).
2. **articles table**: the metadata database's `articles` table, keyed by `source_path`, joined via `content_hash` (same plain SHA-256).
3. **Query time**: search handler collects unique `source_hash` from results, batch-lookups in `articles`, merges `article_title`, `article_authors`, etc. into response metadata.

∀ article metadata changes: no reindex needed — update the `articles` table and search results reflect it immediately.

## Storage Layout

| Path | Contents |
|------|----------|
| Vector store dir | ChromaDB vector data |
| Metadata database | SQLite: properties, articles, corpus_hints, scope_vocabulary, watermarks |

## articles Table (Source of Truth)

The `articles` table is the runtime source of truth for citation metadata. A
YAML staging/curation file is used for authoring only, not runtime truth.

Population:
- Bulk (scope-aware): a backfill script maps subdirectory→scope via the gateway (idempotent).
- Bulk (scope=all): a populate-articles script (idempotent; supports dry-run, direct RAG URL).
- Single: `POST /article` on RAG service, or the equivalent MCP upsert-article op (merge semantics — non-empty fields overwrite).
- Gateway passthrough: `POST /api/v1/rag/article`

## Clean-Slate Reindex

```bash
rm -rf ~/.rag/*
python scripts/backfill_article_metadata.py
./manage
```

## Event-Driven Admission (Gateway Coordination)

**Invariant**: ∀ index-time LLM calls during bulk indexing: gated by an
admission-gate package, which subscribes to gateway coordination signals —
both capacity admission (`capacity.admission.paused`/`resumed`, the
starvation-drain preemption primitive) **and** model lifecycle
(`model.loading.started`/`loaded`/`load.failed`, the cold-load coordination
surface) — for the configured contextualize model. ¬ hand-rolled concurrency
caps; ¬ wall-clock backoff.

Why both signal families: `capacity.admission.*` alone fires only when the
gateway preempts admission to relieve starvation on a competing model. It
does not fire for an ordinary cold load of the target model.
`model.loading.started`/`loaded` close that gap. `model.load.failed` reopens
the gate so the next request triggers a retry instead of leaving workers
stuck CLOSED until each hits its client timeout.

| Component | Purpose |
|---|---|
| Admission gate | Subscribes to admission + model-lifecycle signals; exposes `wait_for_admission(model_id, timeout)` |
| Entity admission gate | Fail-closed entity backing gate for entity-gated watch roots (legal, evidence); layered admitted-paths set |

Lifecycle: constructed at service startup (when a contextualize model is
set), stored on service state, stopped at shutdown.

Default state: OPEN. The first cold-load batch will produce a bounded
burst (≤ N requests, where N = per-file workers × concurrent files,
typically ≤ 32–64) into the gateway's capacity-pool queue before
`model.loading.started` arrives and CLOSEs the gate. Subsequent batches
coordinate properly. Operators sizing queue depth should account for this
burst.

Worker pattern:
```python
if admission_gate is not None:
    await admission_gate.wait_for_admission(model, timeout=client_timeout_s)
result = await _call_llm(...)
```

Per-request correctness backstop: the gateway enforces a request-timeout
header server-side on every request, not just pipeline-internal calls.

∀ new index-time LLM steps: subscribe via the admission gate; do not
re-introduce hand-rolled concurrency caps.

## Key Invariants

- `source_hash` on chunks = plain `hashlib.sha256(raw).hexdigest()` (no schema version)
- `content_hash` in chunk IDs = hash including schema version (for staleness detection)
- Deprecated legacy article-registry-path config option — do not use
- Article metadata is never baked into chunks at index time
<!-- /target:* -->
