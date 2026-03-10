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
    path: str
    extensions: list[str]
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
    """

    pipeline: str = "rag-extraction"
    schema_version: int = 1
    property_boost_factor: float = 0.5
    max_extraction_attempts: int = 3
    # ∀ chunk: attempt_count >= max_extraction_attempts ⟹ permanently skipped


DEFAULT_EMBEDDING_MODEL = "bge-m3-q8-0-8192-cpu"


@dataclass(slots=True, kw_only=True)
class RagConfig:
    watch_directories: list[WatchDirectory]
    scopes: dict[str, ScopeDefinition]
    automatic_indexing_enabled: bool = True
    knowledge_extraction: KnowledgeExtractionConfig = _field(
        default_factory=KnowledgeExtractionConfig
    )
    embedding_model: str = DEFAULT_EMBEDDING_MODEL


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
        recursive_value = item.get("recursive", True)
        recursive = recursive_value if isinstance(recursive_value, bool) else True
        raw_chunk_tokens = item.get("chunk_tokens")
        chunk_tokens = raw_chunk_tokens if isinstance(raw_chunk_tokens, int) else None
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
    if not isinstance(raw, dict):
        return KnowledgeExtractionConfig()
    pipeline = raw.get("pipeline", "rag-extraction")
    schema_version = raw.get("schema_version", 1)
    boost = raw.get("property_boost_factor", 0.5)
    max_attempts = raw.get("max_extraction_attempts", 3)
    return KnowledgeExtractionConfig(
        pipeline=str(pipeline) if isinstance(pipeline, str) else "rag-extraction",
        schema_version=int(schema_version) if isinstance(schema_version, int) else 1,
        property_boost_factor=float(boost) if isinstance(boost, int | float) else 0.5,
        max_extraction_attempts=int(max_attempts)
        if isinstance(max_attempts, int)
        else 3,
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
        return RagConfig(watch_directories=[], scopes={})

    try:
        loaded: object = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.error("Failed to parse RAG config: path=%s", config_path, exc_info=True)
        return RagConfig(watch_directories=[], scopes={})

    if not isinstance(loaded, dict):
        logger.error("Invalid RAG config root type: expected mapping")
        return RagConfig(watch_directories=[], scopes={})
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
    raw_indexing = parsed_root.get("automatic_indexing_enabled", True)
    automatic_indexing_enabled = (
        raw_indexing if isinstance(raw_indexing, bool) else True
    )
    return RagConfig(
        watch_directories=watch_directories,
        scopes=scopes,
        automatic_indexing_enabled=automatic_indexing_enabled,
        knowledge_extraction=knowledge_extraction,
        embedding_model=embedding_model,
    )
