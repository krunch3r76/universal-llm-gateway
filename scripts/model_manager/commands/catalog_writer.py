"""Static catalog file writer for model_manager.

Writes directly to config/models/ in workspace (host filesystem).
Used by generate and measure commands when --static is specified.

Architecture: model_manager owns static catalog writes; Gateway provides data only.

Single responsibility: file location + atomic write.
Callers are responsible for building the complete entry dict.
"""

from pathlib import Path
from typing import Any

from universal_logging import get_logger
from universal_workspace import get_workspace_root

from scripts.catalog_split import determine_model_path, write_model_file

logger = get_logger(__name__)


def get_static_models_dir() -> Path:
    """Get static catalog models directory (config/models/)."""
    workspace = get_workspace_root()
    return workspace / "config" / "models"


def write_static_catalog_entry(
    model_id: str,
    entry: dict[str, Any],
    *,
    allow_overwrite: bool = True,
) -> tuple[Path, str]:
    """
    Write catalog entry to static catalog (config/models/).

    Uses scripts/catalog_split path mapping for domain/engine structure.

    Args:
        model_id: Model identifier
        entry: Complete V2 catalog entry dict (schema, metadata, download, devices, loader)
        allow_overwrite: If False, raises if file exists

    Returns:
        Tuple of (file_path, operation) where operation is "created" or "updated"

    Raises:
        FileExistsError: If file exists and allow_overwrite=False
        ValueError: If entry missing required 'schema' field
    """
    if "schema" not in entry:
        raise ValueError(f"Entry for '{model_id}' missing required 'schema' field")

    domain_engine = determine_model_path(model_id, entry)
    models_dir = get_static_models_dir()
    file_path = models_dir / domain_engine / f"{model_id}.yaml"

    operation = "updated" if file_path.exists() else "created"

    if file_path.exists() and not allow_overwrite:
        raise FileExistsError(f"Model '{model_id}' already exists at {file_path}")

    # write_model_file handles directory creation and atomic write
    write_model_file(file_path, entry)

    logger.info(f"{operation.title()} static catalog entry: {file_path}")
    return file_path, operation
