from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path.home() / ".gateway" / "rag.yaml"


@dataclass(slots=True, kw_only=True)
class WatchDirectory:
    """A directory watched for RAG indexing.

    chunk_tokens: Target size per chunk in tokens (≈4 chars/token). When None,
    the chunker uses its built-in default (1024 for docs, 256 for code). Set
    to 512 or 1024 for larger chunks to improve retrieval of coherent
    paragraphs; re-index (or touch files) after changing.
    """

    path: str
    extensions: list[str] = field(default_factory=list)
    recursive: bool = True
    chunk_tokens: int | None = None
    exclude: list[str] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class ScopeDefinition:
    """A named subset of indexed content for scoped retrieval."""

    prefixes: list[str]
    description: str = ""


@dataclass(slots=True, kw_only=True)
class KnowledgeExtractionConfig:
    """LLM-based knowledge extraction at index time.

    Extraction is integral to indexing — ∀ indexed file: extraction runs.
    To skip extraction entirely, disable indexing (automatic_indexing_enabled: false).
    Per-batch HTTP timeouts scale with chunk count to fail fast under model
    saturation when multiple index workers fan out to the same backend.
    """

    pipeline: str = "rag-extraction"
    schema_version: int = 1
    property_boost_factor: float = 0.5
    max_extraction_attempts: int = 3
    # ∀ chunk: attempt_count >= max_extraction_attempts ⟹ permanently skipped
    extraction_model: str = (
        ""  # Stored in chunk metadata; mismatch triggers re-extraction
    )
    per_chunk_timeout_s: float = 60.0
    batch_timeout_overhead_s: float = 30.0
    # How long extraction workers wait for model.loaded before giving up.
    # Must exceed cold model load time (14B model: ~2-5 min). Default: 10 min.
    model_load_wait_s: float = 600.0


DEFAULT_EMBEDDING_MODEL = "qwen3-embedding-8b-q8-0-4096"
DEFAULT_INDEX_WORKERS = 8
# Used when contextualize_model is omitted; set to "" to disable contextualization.
DEFAULT_CONTEXTUALIZE_MODEL = "qwen3-5-9b-q8-0-262144"
_BASELINE_EXTENSIONS: tuple[str, ...] = (
    ".md",
    ".txt",
    ".html",
    ".htm",
    ".pdf",
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
    # Model ID for per-chunk context generation before embedding. Omitted → use default (on).
    # Set to "" to explicitly disable contextualization.
    contextualize_model: str = DEFAULT_CONTEXTUALIZE_MODEL
    # Seconds between watcher reconcile sweeps (recover files missed by inotify). 0 = disabled.
    # Higher values reduce idle CPU; default 300 (5 min).
    reconcile_interval_s: float = 300.0
    # Default for scripts/rag/classify_vocabulary.py when calling vocab-classify-v1 pipeline.
    vocabulary_mode: str = "local"
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


def save_scope(
    name: str,
    prefixes: list[str],
    description: str = "",
    *,
    watch: bool = False,
) -> None:
    """Persist a scope (and optional watch dirs) to ~/.gateway/rag.yaml.

    Reads the existing YAML, merges the scope into the ``scopes`` mapping,
    and optionally appends watch_directories entries for each prefix. Writes
    back atomically via a temp file to avoid partial-write corruption.

    Returns:
        None
    """
    import tempfile

    config_path = _CONFIG_PATH
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    else:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    scopes_section = raw.setdefault("scopes", {})
    scopes_section[name] = {"prefixes": prefixes, "description": description}

    if watch:
        watchers = raw.setdefault("watch_directories", [])
        existing_paths = {w.get("path") for w in watchers if isinstance(w, dict)}
        for pfx in prefixes:
            if pfx not in existing_paths:
                watchers.append({"path": pfx, "recursive": True})

    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(config_path.parent), suffix=".yaml.tmp")
    try:
        import os

        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
        Path(tmp_path).replace(config_path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    logger.info("Persisted scope '%s' to %s", name, config_path)


def _normalize_extensions(raw_extensions: object) -> list[str]:
    if not isinstance(raw_extensions, list):
        return []
    return [
        f".{item.strip().lstrip('.')}"
        for item in raw_extensions
        if isinstance(item, str) and item.strip()
    ]


def _parse_watch_directories(raw_watchers: object) -> list[WatchDirectory]:
    if not isinstance(raw_watchers, list):
        logger.error("Invalid watch_directories type: expected list")
        return []

    watch_directories: list[WatchDirectory] = []
    for raw_item in raw_watchers:
        if not isinstance(raw_item, dict):
            logger.warning("Skipping watch entry with invalid type: %r", raw_item)
            continue
        item: dict[str, object] = {
            key: value for key, value in raw_item.items() if isinstance(key, str)
        }
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            logger.warning("Skipping watch entry without valid path: %r", item)
            continue
        recursive_raw = item.get("recursive", True)
        recursive = recursive_raw if isinstance(recursive_raw, bool) else True
        chunk_tokens_raw = item.get("chunk_tokens")
        chunk_tokens = chunk_tokens_raw if isinstance(chunk_tokens_raw, int) else None
        raw_exclude = item.get("exclude")
        exclude = [
            e
            for e in (raw_exclude if isinstance(raw_exclude, list) else [])
            if isinstance(e, str) and e.strip()
        ]
        watch_directories.append(
            WatchDirectory(
                path=path.strip(),
                extensions=_normalize_extensions(item.get("extensions")),
                recursive=recursive,
                chunk_tokens=chunk_tokens,
                exclude=exclude,
            )
        )
    return watch_directories


def _normalize_scope_prefixes(scope_name: str, prefixes: object) -> list[str]:
    if not isinstance(prefixes, list):
        logger.warning("Skipping scope '%s': prefixes must be a list", scope_name)
        return []
    normalized = [
        prefix.strip()
        for prefix in prefixes
        if isinstance(prefix, str) and prefix.strip()
    ]
    if not normalized:
        logger.warning("Skipping scope '%s': no valid prefixes", scope_name)
        return []
    return normalized


def _parse_scopes(raw_scopes: object) -> dict[str, ScopeDefinition]:
    if not isinstance(raw_scopes, dict):
        logger.error("Invalid scopes type: expected mapping")
        return {}

    # First pass: collect all prefixes from explicitly defined (non-union) scopes
    # so union scopes can include prefixes defined anywhere in the config.
    all_explicit_prefixes: set[str] = set()
    for scope_name, scope_data in raw_scopes.items():
        if not isinstance(scope_name, str) or not isinstance(scope_data, dict):
            continue
        if scope_data.get("union") is True:
            continue
        prefixes = _normalize_scope_prefixes(scope_name, scope_data.get("prefixes"))
        if prefixes:
            all_explicit_prefixes.update(prefixes)

    scopes: dict[str, ScopeDefinition] = {}
    for scope_name, scope_data in raw_scopes.items():
        if not isinstance(scope_name, str) or not isinstance(scope_data, dict):
            logger.warning("Skipping scope with invalid structure: %r", scope_name)
            continue

        description = scope_data.get("description", "")
        description = description if isinstance(description, str) else ""

        if scope_data.get("union") is True:
            scopes[scope_name] = ScopeDefinition(
                prefixes=sorted(all_explicit_prefixes),
                description=description,
            )
        else:
            prefixes = _normalize_scope_prefixes(scope_name, scope_data.get("prefixes"))
            if not prefixes:
                continue
            scopes[scope_name] = ScopeDefinition(
                prefixes=prefixes,
                description=description,
            )
    return scopes


def _parse_knowledge_extraction(raw: object) -> KnowledgeExtractionConfig:
    """Parse knowledge_extraction config with validation and safe clamping.

    Missing or malformed fields fall back to defaults. Concurrency is clamped
    to at least one, per-chunk timeout to at least one second, and timeout
    overhead to a non-negative value.
    """
    if not isinstance(raw, dict):
        return KnowledgeExtractionConfig()
    pipeline = raw.get("pipeline", "rag-extraction")
    schema_version = raw.get("schema_version", 1)
    boost = raw.get("property_boost_factor", 0.5)
    max_attempts = raw.get("max_extraction_attempts", 3)
    extraction_model = str(raw.get("extraction_model", ""))
    per_chunk_timeout_s = max(1.0, float(raw.get("per_chunk_timeout_s", 60.0)))
    batch_timeout_overhead_s = max(
        0.0, float(raw.get("batch_timeout_overhead_s", 30.0))
    )
    model_load_wait_s = max(30.0, float(raw.get("model_load_wait_s", 600.0)))
    return KnowledgeExtractionConfig(
        pipeline=str(pipeline),
        schema_version=int(schema_version),
        property_boost_factor=float(boost),
        max_extraction_attempts=int(max_attempts),
        extraction_model=extraction_model,
        per_chunk_timeout_s=per_chunk_timeout_s,
        batch_timeout_overhead_s=batch_timeout_overhead_s,
        model_load_wait_s=model_load_wait_s,
    )


def _resolve_config_path() -> Path | None:
    """Return ~/.gateway/rag.yaml if it exists, otherwise None."""
    if _CONFIG_PATH.exists():
        return _CONFIG_PATH
    return None


def load_config() -> RagConfig:
    """Load ~/.gateway/rag.yaml and return parsed config."""
    config_path = _resolve_config_path()
    if config_path is None:
        raise ValueError("rag.yaml not found at ~/.gateway/rag.yaml")

    try:
        loaded: object = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        logger.error("Failed to parse RAG config: path=%s", config_path, exc_info=True)
        raise ValueError(
            f"Critical: Failed to load RAG configuration from {config_path}: {e}"
        ) from e

    if not isinstance(loaded, dict):
        logger.error("Invalid RAG config root type: expected mapping")
        raise ValueError("rag.yaml root must be a mapping")
    parsed_root: dict[str, object] = {
        key: value for key, value in loaded.items() if isinstance(key, str)
    }

    watch_directories = _parse_watch_directories(
        parsed_root.get("watch_directories", [])
    )
    scopes = _parse_scopes(parsed_root.get("scopes", {}))
    knowledge_extraction = _parse_knowledge_extraction(
        parsed_root.get("knowledge_extraction", {})
    )
    embedding_model = (
        parsed_root.get("embedding_model", DEFAULT_EMBEDDING_MODEL).strip()
        if isinstance(parsed_root.get("embedding_model", DEFAULT_EMBEDDING_MODEL), str)
        and parsed_root.get("embedding_model", DEFAULT_EMBEDDING_MODEL).strip()
        else DEFAULT_EMBEDDING_MODEL
    )
    raw_index_workers = parsed_root.get("index_workers", DEFAULT_INDEX_WORKERS)
    index_workers = (
        raw_index_workers
        if isinstance(raw_index_workers, int) and raw_index_workers > 0
        else DEFAULT_INDEX_WORKERS
    )
    raw_indexing = parsed_root.get("automatic_indexing_enabled", True)
    automatic_indexing_enabled = (
        raw_indexing if isinstance(raw_indexing, bool) else True
    )
    article_registry_path: Path | None = None
    raw_registry = parsed_root.get("article_registry_path")
    if isinstance(raw_registry, str) and raw_registry.strip():
        article_registry_path = Path(raw_registry.strip()).expanduser()
    raw_enforcement = parsed_root.get("post_index_enforcement", "strict")
    post_index_enforcement = (
        raw_enforcement
        if isinstance(raw_enforcement, str) and raw_enforcement in ("warn", "strict")
        else "strict"
    )
    raw_ctx_model = parsed_root.get("contextualize_model")
    if raw_ctx_model is None:
        contextualize_model = DEFAULT_CONTEXTUALIZE_MODEL
    elif isinstance(raw_ctx_model, str) and not raw_ctx_model.strip():
        contextualize_model = ""  # Explicitly disabled
    elif isinstance(raw_ctx_model, str):
        contextualize_model = raw_ctx_model.strip()
    else:
        contextualize_model = DEFAULT_CONTEXTUALIZE_MODEL
    raw_reconcile = parsed_root.get("reconcile_interval_s", 300.0)
    if isinstance(raw_reconcile, int | float) and raw_reconcile >= 0:
        reconcile_interval_s = float(raw_reconcile)
    else:
        reconcile_interval_s = 300.0
    raw_file_timeout = parsed_root.get("file_timeout_s", 600.0)
    if isinstance(raw_file_timeout, int | float) and raw_file_timeout >= 0:
        file_timeout_s = float(raw_file_timeout)
    else:
        file_timeout_s = 600.0
    raw_vocab_mode = parsed_root.get("vocabulary_mode", "local")
    if isinstance(raw_vocab_mode, str) and raw_vocab_mode.strip().lower() in (
        "local",
        "frontier",
    ):
        vocabulary_mode = raw_vocab_mode.strip().lower()
    else:
        vocabulary_mode = "local"
    return RagConfig(
        watch_directories=watch_directories,
        scopes=scopes,
        automatic_indexing_enabled=automatic_indexing_enabled,
        knowledge_extraction=knowledge_extraction,
        embedding_model=embedding_model,
        index_workers=index_workers,
        article_registry_path=article_registry_path,
        baseline_extensions=BASELINE_EXTENSIONS,
        post_index_enforcement=post_index_enforcement,
        contextualize_model=contextualize_model,
        reconcile_interval_s=reconcile_interval_s,
        file_timeout_s=file_timeout_s,
        vocabulary_mode=vocabulary_mode,
    )
