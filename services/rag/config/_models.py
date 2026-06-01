"""RAG configuration dataclasses and module-level defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True, kw_only=True)
class WatchDirectory:
    """A directory watched for RAG indexing.

    chunk_tokens: Target size per chunk in tokens (≈4 chars/token). When None,
    the chunker uses its built-in default (1024 for docs, 256 for code). Set
    to 512 or 1024 for larger chunks to improve retrieval of coherent
    paragraphs; re-index (or touch files) after changing.
    exclude: fnmatch globs matched against the watch-root-relative path
    (e.g. ``trading/**``) and bare filename globs matched against basenames
    (e.g. ``CORPUS_MANIFEST.md``).
    """

    path: str
    extensions: list[str] = field(default_factory=list)
    recursive: bool = True
    chunk_tokens: int | None = None
    exclude: list[str] = field(default_factory=list)
    # When True, RAG indexes a file under this root only if a cortex entity
    # points at it via source_uri (thread 1136 A2). Default False preserves
    # today's filesystem-open behaviour for bulk corpora; set True for legal
    # and evidence in ~/.gateway/rag.yaml.
    entity_gated: bool = False


@dataclass(slots=True, kw_only=True)
class ScopeDefinition:
    """A named subset of indexed content for scoped retrieval."""

    prefixes: list[str]
    description: str = ""
    vocab_mode: str = (
        ""  # "local" | "frontier" | "none" (skip) | "" (inherit global vocabulary_mode)
    )
    # True when this scope was declared union: true in rag.yaml — prefixes are the
    # union of all explicitly defined scopes. Corpus hints and vocabulary
    # classification are skipped: IDF ≈ 0 for high-frequency terms across all
    # paths, so results are structurally noise.
    is_union: bool = False


@dataclass(slots=True, kw_only=True)
class KnowledgeExtractionConfig:
    """LLM-based knowledge extraction at index time.

    Extraction is integral to indexing except for explicitly excluded scopes.
    Use ``exclude_scopes`` for corpora like Persian poetry where the extraction
    prompts are known to be a bad fit.
    Per-batch HTTP timeouts scale with chunk count to fail fast under model
    saturation when multiple index workers fan out to the same backend.
    """

    pipeline: str = "rag-extraction"
    schema_version: int = 1
    property_boost_factor: float = 0.5
    max_extraction_attempts: int = 3
    # ∀ chunk: attempt_count >= max_extraction_attempts ⟹ permanently skipped
    # Derived at config-load time from the pipeline's models.yaml (not user-configured).
    # Stored in chunk metadata; mismatch triggers re-extraction.
    extraction_model: str = ""
    per_chunk_timeout_s: float = 60.0
    batch_timeout_overhead_s: float = 30.0
    # How long extraction workers wait for model.loaded before giving up.
    # Must exceed cold model load time (14B model: ~2-5 min). Default: 10 min.
    model_load_wait_s: float = 600.0
    exclude_scopes: list[str] = field(default_factory=list)

    def should_extract_scope(self, scope: str) -> bool:
        """Return whether extraction should run for the resolved scope."""
        return scope not in self.exclude_scopes


DEFAULT_EMBEDDING_MODEL = "qwen3-embedding-8b-q8-0-4096"
DEFAULT_INDEX_WORKERS = 8
# Outer HTTP ceiling for one contextualize pipeline call (queue wait + inference).
DEFAULT_CONTEXTUALIZE_CLIENT_TIMEOUT_S = 3600.0
_BASELINE_EXTENSIONS: tuple[str, ...] = (
    ".md",
    ".txt",
    ".html",
    ".htm",
    ".pdf",
    ".docx",
    ".doc",
)
# Public alias for cross-module baseline defaults.
BASELINE_EXTENSIONS: tuple[str, ...] = _BASELINE_EXTENSIONS


@dataclass(slots=True, kw_only=True)
class RagConfig:
    """Parsed RAG service configuration for indexing, retrieval, and extraction.

    ``article_registry_path`` is retained for one-time startup migration of
    legacy YAML article metadata into SQLite; after all deployments migrate, the
    field should be removed with its import path.
    """

    watch_directories: list[WatchDirectory]
    scopes: dict[str, ScopeDefinition]
    automatic_indexing_enabled: bool = True
    knowledge_extraction: KnowledgeExtractionConfig = field(
        default_factory=KnowledgeExtractionConfig
    )
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    index_workers: int = DEFAULT_INDEX_WORKERS
    # Optional path to article_registry.yaml (filename → citation metadata for chunk enrichment).
    article_registry_path: Path | None = None
    baseline_extensions: tuple[str, ...] = BASELINE_EXTENSIONS
    post_index_enforcement: str = "strict"
    # Model ID for per-chunk context generation before embedding.
    # Derived at config-load time from pipelines/rag_contextualize/models.yaml (not user-configured).
    # Set to "" to disable contextualization.
    contextualize_model: str = ""
    # Pipeline ID for contextualization dispatch.
    contextualize_pipeline: str = "rag-contextualize"
    # Outer HTTP ceiling for one contextualize pipeline call (queue wait + all-chunk inference).
    contextualize_client_timeout_s: float = DEFAULT_CONTEXTUALIZE_CLIENT_TIMEOUT_S
    # Seconds between watcher reconcile sweeps (recover files missed by inotify). 0 = disabled.
    # Higher values reduce idle CPU; default 300 (5 min).
    reconcile_interval_s: float = 300.0
    # Separate worker limit for reconcile sweeps. Prevents reconcile retries
    # from saturating the model queue and crowding out fresh indexing work.
    # Defaults to 3 (independent of index_workers).
    reconcile_workers: int = 3
    # Local model for vocabulary classification (vocabulary_mode=local paths).
    # Derived at config-load time from pipelines/vocab_classify/models.yaml domain_discovery.model
    # (not user-configured). Empty string means vocab_classify/models.yaml is missing or
    # unreadable; local-mode scopes are skipped with rag.vocabulary.gaps.detected
    # reason="no_model_configured".
    vocabulary_model: str = ""
    # Default for scripts/rag/classify_vocabulary.py when calling vocab-classify-v1 pipeline.
    vocabulary_mode: str = "local"
    # Ordered list of vocabulary categories for classification. Order defines retrieval
    # anchor priority (index 0 = highest). The default order reflects IDF selectivity:
    # specification terms (named standards/protocols) are rare in a research corpus →
    # high IDF → precise anchors. Academic terms (theoretical concepts, named models)
    # appear in most papers → low IDF → anchoring on them filters almost nothing.
    # Extend this list when adding corpus domains with distinct vocabulary — no code
    # changes needed, re-classify the affected scope(s) to take effect.
    vocabulary_taxonomy: list[str] = field(
        default_factory=lambda: ["specification", "practitioner", "academic"]
    )
    # Per-file timeout for watcher workers (initial reindex + reconcile). 0 = no timeout.
    # Prevents a single hung extraction from blocking an entire watcher worker indefinitely.
    file_timeout_s: float = 600.0

    def get_scope_for_path(self, file_path: str) -> str:
        """Longest-prefix match over scopes; leaf-preferred on ties.

        When multiple scopes match at the same prefix length, prefer the
        scope with fewer total prefixes (more specific leaf scope) over
        umbrella scopes that aggregate many directories.

        Returns:
            The name of the best-matching scope, or 'all' if no scope matches.
        """
        best_length = 0
        candidates: list[tuple[str, int]] = []
        for scope_name, scope_def in self.scopes.items():
            for prefix in scope_def.prefixes:
                if file_path.startswith(prefix):
                    pl = len(prefix)
                    if pl > best_length:
                        best_length = pl
                        candidates = [(scope_name, len(scope_def.prefixes))]
                    elif pl == best_length:
                        candidates.append((scope_name, len(scope_def.prefixes)))
        if not candidates:
            return "all"
        if len(candidates) == 1:
            return candidates[0][0]
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]

    def is_path_entity_gated(self, file_path: str) -> bool:
        """Longest-prefix match over watch roots; return the matched root's flag.

        Layer-2 authority (thread 1136 A2): the index funnel has only the
        absolute source path, not the WatchDirectory, so it resolves the
        governing root here. WatchDirectory.path is already resolved at parse
        time (config/_parsing._resolve_path), so this is pure string work — no
        filesystem syscall. A nested child watch root overrides a broader
        parent's policy (mirrors get_scope_for_path's longest-prefix discipline).
        The trailing-separator guard is intentionally stricter than
        get_scope_for_path so e.g. '/legal-archive' does not match a '/legal'
        root.

        Consulted only on the index funnel (inotify / admin / changed file),
        not per-file in sweeps — sweeps decide via the entity_gated flag threaded
        into _should_attempt.
        """
        best_length = -1
        gated = False
        for wd in self.watch_directories:
            root = wd.path.rstrip("/")
            if (file_path == root or file_path.startswith(root + "/")) and len(
                root
            ) > best_length:
                best_length = len(root)
                gated = wd.entity_gated
        return gated
