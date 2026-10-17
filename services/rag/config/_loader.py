"""RAG config loading and persistence: load_config and save_scope.

Both are re-exported from ``services.rag.config``. ``load_config`` is called by
``rag_service/lifecycle.py``, ``admin_routes/articles.py``,
``corpus_hints/cli.py`` and ``property_index/__main__.py``; ``save_scope`` by
``rag_service/api.py``. Contextualize and vocabulary model IDs come from
pipeline ``models.yaml`` files, never from ``rag.yaml``, to avoid split-brain.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from ._models import (
    BASELINE_EXTENSIONS,
    DEFAULT_CONTEXTUALIZE_CLIENT_TIMEOUT_S,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_INDEX_WORKERS,
    DEFAULT_QUERY_EMBED_REQUEST_TIMEOUT_S,
    RagConfig,
)
from ._parsing import (
    _parse_knowledge_extraction,
    _parse_scopes,
    _parse_watch_directories,
    _pipeline_contextualize_model,
    _pipeline_embedding_model,
    _pipeline_vocab_model,
    _resolve_path,
)

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path.home() / ".gateway" / "rag.yaml"
_TRACKED_MEMBERSHIP_PATH = Path(__file__).with_name("scope_membership.yaml")


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


def _tracked_prefix_strings(scope_name: str, entry: object) -> list[str] | None:
    """Return usable tracked prefix strings, or None when this delta is skipped.

    A non-mapping entry, a non-list ``prefixes`` value, or a list with no
    non-empty string is logged and skipped. The box scope, when present, stays.
    """
    if not isinstance(entry, dict):
        logger.warning(
            "Skipping tracked scope '%s': entry must be a mapping",
            scope_name,
        )
        return None
    prefixes = entry.get("prefixes")
    if not isinstance(prefixes, list):
        logger.warning(
            "Skipping tracked scope '%s': prefixes must be a list",
            scope_name,
        )
        return None
    usable = [
        prefix.strip()
        for prefix in prefixes
        if isinstance(prefix, str) and prefix.strip()
    ]
    if not usable:
        logger.warning(
            "Skipping tracked scope '%s': no non-empty string prefix",
            scope_name,
        )
        return None
    return usable


def _load_tracked_scopes(path: Path) -> dict[object, object] | None:
    """Return the tracked ``scopes`` mapping, or None to keep the box scopes.

    Called only after the box file has parsed to a mapping. A missing path is
    a warning. ``UnicodeDecodeError``, ``OSError``, ``yaml.YAMLError``, a
    non-mapping root, and a non-mapping ``scopes`` value are errors. None of
    those raise: rag startup continues on the box membership. Top-level keys
    other than ``scopes`` are ignored and named in a warning.
    """
    if not path.exists():
        logger.warning("Tracked scope membership missing: path=%s", path)
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        logger.error(
            "Failed to read tracked scope membership: path=%s error=%s",
            path,
            type(exc).__name__,
        )
        return None
    try:
        loaded: object = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.error(
            "Failed to parse tracked scope membership: path=%s error=%s",
            path,
            type(exc).__name__,
        )
        return None
    if not isinstance(loaded, dict):
        logger.error(
            "Tracked scope membership root must be a mapping: path=%s",
            path,
        )
        return None
    ignored = sorted(str(key) for key in loaded if key != "scopes")
    if ignored:
        logger.warning(
            "Ignoring non-scope keys in tracked scope membership: %s",
            ignored,
        )
    scopes = loaded.get("scopes", {})
    if not isinstance(scopes, dict):
        logger.error(
            "Tracked scope membership scopes must be a mapping: path=%s",
            path,
        )
        return None
    return scopes


def _merge_tracked_scopes(
    box_scopes: object,
    tracked_scopes: dict[object, object],
) -> object:
    """Union tracked prefixes onto box scopes. Box order, then new paths.

    A resolved tracked path already present is dropped and the box string is
    kept. ``union: true`` entries are left untouched. Description, vocab mode,
    and every other box key stay. A tracked scope the box does not name is
    added. Nothing in the tracked file deletes a box scope or a box prefix.
    """
    if not isinstance(box_scopes, dict):
        return box_scopes
    merged: dict[object, object] = {}
    for key, value in box_scopes.items():
        if isinstance(value, dict):
            entry = dict(value)
            prefixes = entry.get("prefixes")
            if isinstance(prefixes, list):
                entry["prefixes"] = list(prefixes)
            merged[key] = entry
        else:
            merged[key] = value
    for name, tracked_entry in tracked_scopes.items():
        if not isinstance(name, str):
            logger.warning("Skipping tracked scope with a non-string name")
            continue
        if name in merged:
            box_entry = merged[name]
            if isinstance(box_entry, dict) and box_entry.get("union") is True:
                logger.warning(
                    "Skipping tracked prefixes for union scope '%s'",
                    name,
                )
                continue
        usable = _tracked_prefix_strings(name, tracked_entry)
        if usable is None or not isinstance(tracked_entry, dict):
            continue
        if name not in merged:
            added = dict(tracked_entry)
            added["prefixes"] = usable
            merged[name] = added
            continue
        box_entry = merged[name]
        if not isinstance(box_entry, dict):
            logger.warning(
                "Skipping tracked scope '%s': box entry is not a mapping",
                name,
            )
            continue
        box_prefixes = box_entry.get("prefixes")
        if not isinstance(box_prefixes, list):
            logger.warning(
                "Skipping tracked scope '%s': box prefixes are not a list",
                name,
            )
            continue
        ignored_keys = sorted(str(key) for key in tracked_entry if key != "prefixes")
        if ignored_keys:
            logger.warning(
                "Ignoring tracked scope keys for '%s': %s",
                name,
                ignored_keys,
            )
        resolved_box = {
            _resolve_path(prefix.strip())
            for prefix in box_prefixes
            if isinstance(prefix, str) and prefix.strip()
        }
        kept = list(box_prefixes)
        for prefix in usable:
            resolved = _resolve_path(prefix)
            if resolved in resolved_box:
                continue
            kept.append(prefix)
            resolved_box.add(resolved)
        box_entry["prefixes"] = kept
    return merged


def load_config() -> RagConfig:
    """Read ``~/.gateway/rag.yaml`` and union tracked scope prefixes.

    The box file is required. A missing box file, YAML that does not parse, or
    a non-mapping root raises ``ValueError``. An empty file stays an empty
    mapping, as before. Only after that mapping is in hand does
    ``scope_membership.yaml`` beside this module add scope prefixes. A missing
    tracked file, ``UnicodeDecodeError``, ``OSError``, bad YAML, or a
    non-mapping root or ``scopes`` value is logged and the box scopes are kept.
    Workers, watch directories, and other non-scope keys stay box-only. No
    caching: each call rereads both files.
    """
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
    raw_scopes = parsed_root.get("scopes", {})
    tracked_scopes = _load_tracked_scopes(_TRACKED_MEMBERSHIP_PATH)
    if tracked_scopes is not None:
        raw_scopes = _merge_tracked_scopes(raw_scopes, tracked_scopes)
    scopes = _parse_scopes(raw_scopes)
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
    raw_query_embed_timeout = parsed_root.get(
        "query_embed_request_timeout_s",
        DEFAULT_QUERY_EMBED_REQUEST_TIMEOUT_S,
    )
    if isinstance(raw_query_embed_timeout, int | float) and raw_query_embed_timeout > 0:
        query_embed_request_timeout_s = float(raw_query_embed_timeout)
    else:
        query_embed_request_timeout_s = DEFAULT_QUERY_EMBED_REQUEST_TIMEOUT_S
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
    yaml_embedding = _pipeline_embedding_model()
    if not yaml_embedding:
        logger.error(
            "pipelines/rag/models.yaml embedding alias missing; config embedding_model=%r",
            embedding_model,
        )
    elif embedding_model != yaml_embedding:
        logger.error(
            "rag config embedding_model=%r does not match pipelines/rag/models.yaml "
            "embedding alias %r",
            embedding_model,
            yaml_embedding,
        )

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
        query_embed_request_timeout_s=query_embed_request_timeout_s,
        reconcile_interval_s=reconcile_interval_s,
        reconcile_workers=reconcile_workers,
        file_timeout_s=file_timeout_s,
        vocabulary_model=vocabulary_model,
        vocabulary_mode=vocabulary_mode,
        vocabulary_taxonomy=vocabulary_taxonomy,
    )
