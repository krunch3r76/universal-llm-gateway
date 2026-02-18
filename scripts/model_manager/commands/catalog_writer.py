"""Catalog file writers for model_manager.

Writes directly to host filesystem (static + local catalogs).
Used by generate and measure commands.

Static catalog (config/models/): metadata-only, version-controlled.
Local catalog (~/.gateway/catalog/): full operational entry, per-install.

Single responsibility: file location + atomic write.
Callers are responsible for building the complete entry dict.
"""

from pathlib import Path
from typing import Any

from universal_logging import get_logger
from universal_workspace import get_workspace_root

from scripts.catalog_split import determine_model_path, write_model_file

logger = get_logger(__name__)

_METADATA_STRIP_KEYS = frozenset({"activated_gpu_contexts", "activated_cpu_contexts"})


def get_static_models_dir() -> Path:
    """Get static catalog models directory (config/models/)."""
    workspace = get_workspace_root()
    return workspace / "config" / "models"


def get_local_catalog_dir() -> Path:
    """
    Get local catalog directory (~/.gateway/catalog/).

    Resolution:
        1. GATEWAY_LOCAL_CATALOG_DIR env var
        2. ~/.gateway/catalog/ (default)
    """
    import os

    explicit = os.getenv("GATEWAY_LOCAL_CATALOG_DIR")
    if explicit:
        return Path(explicit)
    return Path.home() / ".gateway" / "catalog"


def strip_measurement_data(entry: dict[str, Any]) -> dict[str, Any]:
    """
    Produce static entry from full operational entry.

    Strips: loader, devices, activated_*_contexts from metadata.
    Adds catalog_schema: 3 as first key.
    """
    metadata = {
        k: v
        for k, v in entry.get("metadata", {}).items()
        if k not in _METADATA_STRIP_KEYS
    }
    static: dict[str, Any] = {"catalog_schema": 3}
    static["schema"] = entry["schema"]
    static["metadata"] = metadata
    if "download" in entry:
        static["download"] = entry["download"]
    return static


def _ensure_catalog_schema(entry: dict[str, Any]) -> dict[str, Any]:
    """Ensure catalog_schema: 3 is the first key in the entry."""
    if list(entry.keys())[:1] == ["catalog_schema"] and entry["catalog_schema"] == 3:
        return entry
    result: dict[str, Any] = {"catalog_schema": 3}
    for k, v in entry.items():
        if k != "catalog_schema":
            result[k] = v
    return result


def write_local_catalog_entry(
    model_id: str,
    entry: dict[str, Any],
    *,
    allow_overwrite: bool = True,
) -> tuple[Path, str]:
    """
    Write full catalog entry to local catalog (~/.gateway/catalog/).

    Args:
        model_id: Model identifier
        entry: Full V3 catalog entry dict (schema, metadata, download, devices, loader)
        allow_overwrite: If False, raises if file exists

    Returns:
        Tuple of (file_path, operation) where operation is "created" or "updated"

    Raises:
        FileExistsError: If file exists and allow_overwrite=False
        ValueError: If entry missing required 'schema' field
    """
    if "schema" not in entry:
        raise ValueError(f"Entry for '{model_id}' missing required 'schema' field")

    local_entry = _ensure_catalog_schema(entry)
    domain_engine = determine_model_path(model_id, entry)
    local_dir = get_local_catalog_dir()
    file_path = local_dir / domain_engine / f"{model_id}.yaml"

    operation = "updated" if file_path.exists() else "created"

    if file_path.exists() and not allow_overwrite:
        raise FileExistsError(f"Model '{model_id}' already exists at {file_path}")

    write_model_file(file_path, local_entry)
    logger.info(f"{operation.title()} local catalog entry: {file_path}")
    return file_path, operation


def write_static_catalog_entry(
    model_id: str,
    entry: dict[str, Any],
    *,
    allow_overwrite: bool = True,
) -> tuple[Path, str]:
    """
    Write metadata-only entry to static catalog (config/models/).

    Strips loader, devices, and activated_*_contexts before writing.

    Args:
        model_id: Model identifier
        entry: Full or static V3 catalog entry dict
        allow_overwrite: If False, raises if file exists

    Returns:
        Tuple of (file_path, operation) where operation is "created" or "updated"

    Raises:
        FileExistsError: If file exists and allow_overwrite=False
        ValueError: If entry missing required 'schema' field
    """
    if "schema" not in entry:
        raise ValueError(f"Entry for '{model_id}' missing required 'schema' field")

    static_entry = strip_measurement_data(entry)
    domain_engine = determine_model_path(model_id, entry)
    models_dir = get_static_models_dir()
    file_path = models_dir / domain_engine / f"{model_id}.yaml"

    operation = "updated" if file_path.exists() else "created"

    if file_path.exists() and not allow_overwrite:
        raise FileExistsError(f"Model '{model_id}' already exists at {file_path}")

    write_model_file(file_path, static_entry)
    logger.info(f"{operation.title()} static catalog entry: {file_path}")
    return file_path, operation
