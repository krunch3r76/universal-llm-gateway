"""RAG config YAML parsing: watch directories, scopes, knowledge extraction."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from ._models import KnowledgeExtractionConfig, ScopeDefinition, WatchDirectory

logger = logging.getLogger(__name__)

# Repo root — three parents up from services/rag/config/.
_REPO_ROOT = Path(__file__).parents[3]


def _pipeline_model(pipeline_dir_name: str, model_ref: str, label: str) -> str:
    """Return the model ID for a named model ref in a pipeline's models.yaml.

    The pipeline's models.yaml is the single source of truth for which model
    (and profile) a step runs. rag.yaml must not duplicate this — that creates
    split-brain when the pipeline model changes without a matching rag.yaml edit.

    Returns "" if the pipeline directory or models.yaml is missing.
    """
    models_path = _REPO_ROOT / "pipelines" / pipeline_dir_name / "models.yaml"
    if not models_path.exists():
        logger.warning(
            "Pipeline models.yaml not found at %s; %s will be empty",
            models_path,
            label,
        )
        return ""
    try:
        with models_path.open() as fh:
            data = yaml.safe_load(fh)
        model = (data or {}).get("models", {}).get(model_ref, {}).get("model", "")
        return str(model) if model else ""
    except Exception as exc:
        logger.warning("Failed to read pipeline models.yaml %s: %s", models_path, exc)
        return ""


def _pipeline_extraction_model(pipeline_id: str) -> str:
    """Return the model ID for the 'extraction' ref in a pipeline's models.yaml."""
    return _pipeline_model(
        pipeline_id.replace("-", "_"), "extraction", "extraction_model"
    )


def _pipeline_contextualize_model() -> str:
    """Return the model ID for the 'contextualize' ref in rag_contextualize/models.yaml."""
    return _pipeline_model("rag_contextualize", "contextualize", "contextualize_model")


def _pipeline_vocab_model() -> str:
    """Return the model ID for the 'domain_discovery' ref in vocab_classify/models.yaml."""
    return _pipeline_model("vocab_classify", "domain_discovery", "vocabulary_model")


def _resolve_path(path_str: str) -> str:
    """Resolve a path to its canonical form, following symlinks.

    Preserves a trailing slash when present — scope prefixes use startswith()
    comparisons where the slash is load-bearing (prevents /foo/bar matching
    /foo/barbaz).
    """
    trailing_slash = path_str.endswith("/")
    resolved = str(Path(path_str).expanduser().resolve())
    return resolved + "/" if trailing_slash else resolved


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
                path=_resolve_path(path.strip()),
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
        _resolve_path(prefix.strip())
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

        raw_vocab_mode = scope_data.get("vocab_mode", "")
        vocab_mode = (
            raw_vocab_mode.strip().lower()
            if isinstance(raw_vocab_mode, str)
            and raw_vocab_mode.strip().lower() in ("local", "frontier", "none", "")
            else ""
        )

        if scope_data.get("union") is True:
            scopes[scope_name] = ScopeDefinition(
                prefixes=sorted(all_explicit_prefixes),
                description=description,
                vocab_mode=vocab_mode,
                is_union=True,
            )
        else:
            prefixes = _normalize_scope_prefixes(scope_name, scope_data.get("prefixes"))
            if not prefixes:
                continue
            scopes[scope_name] = ScopeDefinition(
                prefixes=prefixes,
                description=description,
                vocab_mode=vocab_mode,
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
    extraction_model = _pipeline_extraction_model(str(pipeline))
    per_chunk_timeout_s = max(1.0, float(raw.get("per_chunk_timeout_s", 60.0)))
    batch_timeout_overhead_s = max(
        0.0, float(raw.get("batch_timeout_overhead_s", 30.0))
    )
    model_load_wait_s = max(30.0, float(raw.get("model_load_wait_s", 600.0)))
    raw_exclude_scopes = raw.get("exclude_scopes", [])
    exclude_scopes = (
        [str(scope).strip() for scope in raw_exclude_scopes if str(scope).strip()]
        if isinstance(raw_exclude_scopes, list)
        else []
    )
    return KnowledgeExtractionConfig(
        pipeline=str(pipeline),
        schema_version=int(schema_version),
        property_boost_factor=float(boost),
        max_extraction_attempts=int(max_attempts),
        extraction_model=extraction_model,
        per_chunk_timeout_s=per_chunk_timeout_s,
        batch_timeout_overhead_s=batch_timeout_overhead_s,
        model_load_wait_s=model_load_wait_s,
        exclude_scopes=exclude_scopes,
    )
