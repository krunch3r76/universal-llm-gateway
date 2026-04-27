"""RAG config loading and persistence: load_config and save_scope."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from ._models import (
    BASELINE_EXTENSIONS,
    DEFAULT_CONTEXTUALIZE_CLIENT_TIMEOUT_S,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_INDEX_WORKERS,
    RagConfig,
)
from ._parsing import (
    _parse_knowledge_extraction,
    _parse_scopes,
    _parse_watch_directories,
    _pipeline_contextualize_model,
    _pipeline_vocab_model,
)

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path.home() / ".gateway" / "rag.yaml"


def _resolve_config_path() -> Path | None:
    """Return ~/.gateway/rag.yaml if it exists, otherwise None."""
    if _CONFIG_PATH.exists():
        return _CONFIG_PATH
    return None


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
    # Derived from pipelines/rag_contextualize/models.yaml — not read from rag.yaml.
    contextualize_model = _pipeline_contextualize_model()
    raw_ctx_client_timeout = parsed_root.get(
        "contextualize_client_timeout_s", DEFAULT_CONTEXTUALIZE_CLIENT_TIMEOUT_S
    )
    if isinstance(raw_ctx_client_timeout, int | float) and raw_ctx_client_timeout > 0:
        contextualize_client_timeout_s = float(raw_ctx_client_timeout)
    else:
        contextualize_client_timeout_s = DEFAULT_CONTEXTUALIZE_CLIENT_TIMEOUT_S
    raw_reconcile = parsed_root.get("reconcile_interval_s", 300.0)
    if isinstance(raw_reconcile, int | float) and raw_reconcile >= 0:
        reconcile_interval_s = float(raw_reconcile)
    else:
        reconcile_interval_s = 300.0
    raw_reconcile_workers = parsed_root.get("reconcile_workers", 3)
    if isinstance(raw_reconcile_workers, int) and raw_reconcile_workers >= 1:
        reconcile_workers = raw_reconcile_workers
    else:
        reconcile_workers = 3
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
    # Derived from pipelines/vocab_classify/models.yaml — not read from rag.yaml.
    vocabulary_model = _pipeline_vocab_model()
    raw_taxonomy = parsed_root.get("vocabulary_taxonomy")
    _default_taxonomy = ["specification", "practitioner", "academic"]
    if isinstance(raw_taxonomy, list) and all(
        isinstance(c, str) and c.strip() for c in raw_taxonomy
    ):
        vocabulary_taxonomy = [c.strip() for c in raw_taxonomy]
    else:
        vocabulary_taxonomy = _default_taxonomy
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
        contextualize_client_timeout_s=contextualize_client_timeout_s,
        reconcile_interval_s=reconcile_interval_s,
        reconcile_workers=reconcile_workers,
        file_timeout_s=file_timeout_s,
        vocabulary_model=vocabulary_model,
        vocabulary_mode=vocabulary_mode,
        vocabulary_taxonomy=vocabulary_taxonomy,
    )
