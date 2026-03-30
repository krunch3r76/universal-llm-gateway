"""
Local Catalog Discovery - reads ~/.gateway/catalog/ (or GATEWAY_LOCAL_CATALOG_DIR).

Local catalog contains full operational entries (metadata + loader + devices).
Invalid files are skipped with warning; errors are non-fatal.
"""

import os
from pathlib import Path
from typing import Any

import yaml
from universal_logging import get_logger

logger = get_logger(__name__)

ALLOWED_DOMAINS = frozenset(
    {
        "text_llm",
        "audio",
        "translation",
        "visual",
        "graphics",
        "embedding",
        "reranker",
    }
)


def get_local_catalog_dir() -> Path | None:
    """
    Resolve local catalog directory.

    Resolution:
        1. GATEWAY_LOCAL_CATALOG_DIR env var (production/Docker)
        2. ~/.gateway/catalog/ (default, only if directory exists)

    Returns:
        Path if directory exists and is a directory, None otherwise
    """
    explicit = os.getenv("GATEWAY_LOCAL_CATALOG_DIR")
    if explicit:
        path = Path(explicit)
        if not path.exists():
            logger.debug(f"Local catalog path does not exist: {path}")
            return None
        if not path.is_dir():
            logger.warning(f"Local catalog path is not a directory: {path}")
            return None
        logger.debug(f"Using local catalog: {path}")
        return path

    default_path = Path.home() / ".gateway" / "catalog"
    if default_path.exists() and default_path.is_dir():
        logger.debug(f"Using default local catalog: {default_path}")
        return default_path

    return None


def _has_yaml_files(directory: Path) -> bool:
    """Check if directory contains any YAML files (non-recursive first level check)."""
    try:
        return any(directory.rglob("*.yaml"))
    except OSError:
        return False


def _load_entry(yaml_file: Path) -> dict[str, Any] | None:
    """
    Load single local catalog file.

    Returns:
        Model entry dict, or None if invalid (warning logged)
    """
    try:
        with open(yaml_file, encoding="utf-8") as f:
            entry = yaml.safe_load(f)

        if not entry:
            logger.warning(f"Skipping empty local model file: {yaml_file}")
            return None

        if not isinstance(entry, dict):
            logger.warning(
                f"Skipping local model '{yaml_file.stem}': root must be a dictionary, "
                f"got {type(entry).__name__}"
            )
            return None

        if "schema" not in entry:
            logger.warning(
                f"Skipping local model '{yaml_file.stem}': missing 'schema' field"
            )
            return None

        if "metadata" not in entry:
            logger.warning(
                f"Skipping local model '{yaml_file.stem}': missing 'metadata' field"
            )
            return None

        # Vision models: ensure loader.clip_model_path from download.mmproj_file
        # so entries written by measurement (authoritative local catalog) pass
        # schema validation without requiring a separate static export.
        metadata = entry.get("metadata", {})
        modalities = metadata.get("capabilities", {}).get("modalities", {})
        is_vision = "vision" in modalities.get("input", [])
        if is_vision:
            loader = entry.setdefault("loader", {})
            if not loader.get("clip_model_path"):
                hf = (entry.get("download") or {}).get("huggingface") or {}
                mmproj = hf.get("mmproj_file")
                if mmproj:
                    loader["clip_model_path"] = mmproj

        return entry

    except yaml.YAMLError as e:
        logger.warning(f"Skipping invalid YAML in local model: {yaml_file}: {e}")
        return None
    except OSError as e:
        logger.warning(f"Skipping unreadable local model: {yaml_file}: {e}")
        return None


def _validate_entry(model_id: str, entry: dict[str, Any]) -> bool:
    """
    Validate local model entry.

    Local entries without devices (not yet measured) pass validation.

    Returns:
        True if valid, False if invalid (warning logged)
    """
    from .schemas import SchemaRegistry

    schema = SchemaRegistry.get_for_model(entry)
    if not schema:
        logger.warning(
            f"Skipping local model '{model_id}': unknown schema '{entry.get('schema')}'"
        )
        return False

    issues = schema.validate(model_id, entry)
    errors = [i for i in issues if i.severity == "error"]

    if errors:
        error_msgs = "; ".join(i.message for i in errors[:3])
        logger.warning(
            f"Skipping local model '{model_id}': validation errors: {error_msgs}"
        )
        return False

    return True


def discover_local_models() -> dict[str, Any]:
    """
    Discover models from local catalog directory.

    Invalid files are skipped with warning (not fatal).

    Returns:
        Dict of model_id -> model_entry (may be empty)
    """
    local_dir = get_local_catalog_dir()
    if not local_dir:
        return {}

    models: dict[str, Any] = {}

    for yaml_file in local_dir.rglob("*.yaml"):
        try:
            rel = yaml_file.relative_to(local_dir)
        except ValueError:
            continue

        if not rel.parts:
            continue

        domain = rel.parts[0]
        if domain not in ALLOWED_DOMAINS:
            logger.warning(
                f"Skipping local model in invalid domain: {yaml_file} "
                f"(expected one of {sorted(ALLOWED_DOMAINS)})"
            )
            continue

        model_id = yaml_file.stem

        if model_id in models:
            logger.warning(
                f"Duplicate model ID '{model_id}' in local catalog, "
                f"skipping {yaml_file}"
            )
            continue

        entry = _load_entry(yaml_file)
        if entry is None:
            continue

        if not _validate_entry(model_id, entry):
            continue

        models[model_id] = entry
        logger.debug(f"Loaded local model: {model_id} from {yaml_file}")

    if models:
        logger.info(f"Discovered {len(models)} local model(s)")

    return models
