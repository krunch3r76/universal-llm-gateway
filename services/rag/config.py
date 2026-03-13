from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field as _field
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
    extensions: list[str] = _field(default_factory=list)
    recursive: bool = True
    chunk_tokens: int | None = None
    exclude: list[str] = _field(default_factory=list)


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
    Concurrent extraction batches are bounded by extraction_concurrency to prevent
    model saturation when multiple index workers fan out to the same backend.
    """

    pipeline: str = "rag-extraction"
    schema_version: int = 1
    property_boost_factor: float = 0.5
    max_extraction_attempts: int = 3
    # ∀ chunk: attempt_count >= max_extraction_attempts ⟹ permanently skipped
    extraction_model: str = (
        ""  # Stored in chunk metadata; mismatch triggers re-extraction
    )
    extraction_concurrency: int = 2
    per_chunk_timeout_s: float = 60.0
    batch_timeout_overhead_s: float = 30.0


DEFAULT_EMBEDDING_MODEL = "bge-m3-q8-0-8192-cpu"
DEFAULT_INDEX_WORKERS = 8
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
    watch_directories: list[WatchDirectory]
    scopes: dict[str, ScopeDefinition]
    automatic_indexing_enabled: bool = True
    knowledge_extraction: KnowledgeExtractionConfig = _field(
        default_factory=KnowledgeExtractionConfig
    )
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    index_workers: int = DEFAULT_INDEX_WORKERS
    # Optional path to corpus_hints.yaml (scope → vocabulary hints for suggest_terms).
    corpus_hints_path: Path | None = None
    # Optional path to article_registry.yaml (filename → citation metadata for chunk enrichment).
    article_registry_path: Path | None = None
    baseline_extensions: tuple[str, ...] = BASELINE_EXTENSIONS
    post_index_enforcement: str = "strict"
    contextualize_model: str

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

    scopes: dict[str, ScopeDefinition] = {}
    union_scopes: list[tuple[str, dict[str, object]]] = []
    for scope_name, scope_data in raw_scopes.items():
        if not isinstance(scope_name, str) or not isinstance(scope_data, dict):
            logger.warning("Skipping scope with invalid structure: %r", scope_name)
            continue

        if scope_data.get("union") is True:
            union_scopes.append((scope_name, scope_data))
            continue

        prefixes = _normalize_scope_prefixes(scope_name, scope_data.get("prefixes"))
        if not prefixes:
            continue

        description = scope_data.get("description", "")
        scopes[scope_name] = ScopeDefinition(
            prefixes=prefixes,
            description=description if isinstance(description, str) else "",
        )

    all_prefixes = sorted(
        {prefix for scope in scopes.values() for prefix in scope.prefixes}
    )
    for union_name, union_data in union_scopes:
        description = union_data.get("description", "")
        scopes[union_name] = ScopeDefinition(
            prefixes=all_prefixes,
            description=description if isinstance(description, str) else "",
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
    raw_model = raw.get("extraction_model", "")
    extraction_model = str(raw_model) if isinstance(raw_model, str) else ""
    raw_concurrency = raw.get("extraction_concurrency", 2)
    extraction_concurrency = max(
        1,
        int(raw_concurrency) if isinstance(raw_concurrency, int) else 2,
    )
    raw_per_chunk = raw.get("per_chunk_timeout_s", 60.0)
    per_chunk_timeout_s = max(
        1.0,
        float(raw_per_chunk) if isinstance(raw_per_chunk, int | float) else 60.0,
    )
    raw_overhead = raw.get("batch_timeout_overhead_s", 30.0)
    batch_timeout_overhead_s = max(
        0.0,
        float(raw_overhead) if isinstance(raw_overhead, int | float) else 30.0,
    )
    return KnowledgeExtractionConfig(
        pipeline=str(pipeline) if isinstance(pipeline, str) else "rag-extraction",
        schema_version=int(schema_version) if isinstance(schema_version, int) else 1,
        property_boost_factor=float(boost) if isinstance(boost, int | float) else 0.5,
        max_extraction_attempts=int(max_attempts)
        if isinstance(max_attempts, int)
        else 3,
        extraction_model=extraction_model,
        extraction_concurrency=extraction_concurrency,
        per_chunk_timeout_s=per_chunk_timeout_s,
        batch_timeout_overhead_s=batch_timeout_overhead_s,
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
        return RagConfig(watch_directories=[], scopes={}, contextualize_model="")

    try:
        loaded: object = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.error("Failed to parse RAG config: path=%s", config_path, exc_info=True)
        return RagConfig(watch_directories=[], scopes={}, contextualize_model="")

    if not isinstance(loaded, dict):
        logger.error("Invalid RAG config root type: expected mapping")
        return RagConfig(watch_directories=[], scopes={}, contextualize_model="")
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
    raw_model = parsed_root.get("embedding_model", DEFAULT_EMBEDDING_MODEL)
    embedding_model = (
        raw_model.strip()
        if isinstance(raw_model, str) and raw_model.strip()
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
    corpus_hints_path: Path | None = None
    raw_hints_path = parsed_root.get("corpus_hints_path")
    if isinstance(raw_hints_path, str) and raw_hints_path.strip():
        corpus_hints_path = Path(raw_hints_path.strip()).expanduser()
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
    if not isinstance(raw_ctx_model, str) or not raw_ctx_model.strip():
        raise ValueError(
            "rag.yaml: 'contextualize_model' is required. "
            "Set it to the model ID used for chunk context generation "
            "(e.g. 'qwen3-5-9b-q8-0-262144')."
        )
    contextualize_model = raw_ctx_model.strip()
    return RagConfig(
        watch_directories=watch_directories,
        scopes=scopes,
        automatic_indexing_enabled=automatic_indexing_enabled,
        knowledge_extraction=knowledge_extraction,
        embedding_model=embedding_model,
        index_workers=index_workers,
        corpus_hints_path=corpus_hints_path,
        article_registry_path=article_registry_path,
        baseline_extensions=BASELINE_EXTENSIONS,
        post_index_enforcement=post_index_enforcement,
        contextualize_model=contextualize_model,
    )
