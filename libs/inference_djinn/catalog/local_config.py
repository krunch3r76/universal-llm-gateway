"""
Local Catalog Configuration Management

Provides tooling for managing the user's local dynamic catalog:
  - Export models from static catalog for local customization
  - Add/replace model entries in local catalog
  - Schema validation before writes
  - Merge logic (local shadows static)
"""

import os
from pathlib import Path

import yaml

from .schema import (
    CURRENT_SCHEMA_VERSIONS,
    ConfigType,
    SchemaValidationResult,
    get_catalog_template,
    validate_catalog_schema,
)


class CatalogConfigError(Exception):
    """Error in catalog configuration."""

    pass


class SchemaVersionError(CatalogConfigError):
    """Schema version mismatch error."""

    validation_result: SchemaValidationResult

    def __init__(self, validation_result: SchemaValidationResult):
        self.validation_result = validation_result
        super().__init__(validation_result.message)


def get_user_config_dir() -> Path:
    """
    Get the user config directory path.

    Checks GATEWAY_USER_CONFIG_DIR env var first, falls back to ~/.gateway.
    """
    env_dir = os.environ.get("GATEWAY_USER_CONFIG_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".gateway"


def get_local_catalog_path() -> Path:
    """Get path to local dynamic catalog file."""
    return get_user_config_dir() / "catalog.yaml"


def load_local_catalog() -> dict[str, object]:
    """
    Load local catalog from user config directory.

    Returns empty catalog template if file doesn't exist.
    Raises SchemaVersionError if schema version is incompatible.
    """
    catalog_path = get_local_catalog_path()

    if not catalog_path.exists():
        return get_catalog_template()

    with open(catalog_path) as f:
        raw = yaml.safe_load(f)
        catalog: dict[str, object] = raw if isinstance(raw, dict) else {}

    # Validate schema version
    validation = validate_catalog_schema(catalog)
    if not validation.valid:
        raise SchemaVersionError(validation)

    return catalog


def save_local_catalog(catalog: dict[str, object]) -> Path:
    """
    Save catalog to local config directory.

    Creates directory if needed. Validates schema before saving.
    Returns path to saved file.
    """
    # Ensure catalog has version
    if "catalog_version" not in catalog:
        catalog["catalog_version"] = CURRENT_SCHEMA_VERSIONS[ConfigType.CATALOG]

    if "catalog_type" not in catalog:
        catalog["catalog_type"] = "dynamic"

    # Validate before saving
    validation = validate_catalog_schema(catalog)
    if not validation.valid:
        raise SchemaVersionError(validation)

    catalog_path = get_local_catalog_path()

    # Create directory if needed
    catalog_path.parent.mkdir(parents=True, exist_ok=True)

    with open(catalog_path, "w") as f:
        yaml.dump(
            catalog, f, default_flow_style=False, sort_keys=False, allow_unicode=True
        )

    return catalog_path


def load_static_catalog(static_catalog_path: Path | None = None) -> dict[str, object]:
    """
    Load the static catalog (model_catalog.yaml).

    Args:
        static_catalog_path: Path to static catalog. If None, uses default location.

    Returns:
        Loaded catalog dictionary.
    """
    if static_catalog_path is None:
        # Try workspace root first (canonical location per BREAKING_CHANGES.md)
        try:
            from universal_workspace import get_static_catalog_path

            static_catalog_path = get_static_catalog_path()
        except (ImportError, RuntimeError, FileNotFoundError):
            pass

        # Fallback to legacy locations (Docker, deprecated service path)
        if static_catalog_path is None:
            candidates = [
                # Docker container path
                Path("/app/config/model_catalog.yaml"),
                # Legacy service-specific path (deprecated)
                Path(__file__).parent.parent.parent.parent
                / "services"
                / "_universal-llm-gateway"
                / "config"
                / "model_catalog.yaml",
            ]
            for candidate in candidates:
                if candidate.exists():
                    static_catalog_path = candidate
                    break

    if static_catalog_path is None or not static_catalog_path.exists():
        raise CatalogConfigError(
            "Static catalog not found. Specify path with --static-catalog."
        )

    with open(static_catalog_path) as f:
        raw = yaml.safe_load(f)
        result: dict[str, object] = raw if isinstance(raw, dict) else {}
        return result


def export_model_to_local(
    model_id: str,
    static_catalog_path: Path | None = None,
    force: bool = False,
) -> Path:
    """
    Export a model from static catalog to local dynamic catalog.

    The complete model definition is copied to local catalog, where it
    will shadow the static definition. User can then customize activated
    contexts, resources, etc.

    Args:
        model_id: Model ID to export
        static_catalog_path: Path to static catalog (uses default if None)
        force: Overwrite existing local entry without prompting

    Returns:
        Path to local catalog file

    Raises:
        CatalogConfigError: If model not found in static catalog
        SchemaVersionError: If schema version mismatch
    """
    # Load static catalog
    static = load_static_catalog(static_catalog_path)
    static_models_raw = static.get("models", {})
    static_models: dict[str, object] = (
        static_models_raw if isinstance(static_models_raw, dict) else {}
    )

    if model_id not in static_models:
        available = list(static_models.keys())[:10]
        suffix = "..." if len(static_models) > 10 else ""
        raise CatalogConfigError(
            f"Model '{model_id}' not found in static catalog. Available: {available}{suffix}"
        )

    # Load or create local catalog
    local = load_local_catalog()

    # Check if already exists
    local_models_raw = local.get("models")
    if not isinstance(local_models_raw, dict):
        local_models_raw = {}
        local["models"] = local_models_raw
    local_models: dict[str, object] = local_models_raw

    if model_id in local_models and not force:
        raise CatalogConfigError(
            f"Model '{model_id}' already exists in local catalog. Use --force to overwrite."
        )

    # Copy model definition
    local_models[model_id] = static_models[model_id]

    # Save
    return save_local_catalog(local)


def add_model_to_local(
    model_id: str,
    model_config: dict[str, object],
    force: bool = False,
) -> Path:
    """
    Add or replace a model in local catalog.

    Args:
        model_id: Model ID
        model_config: Complete model configuration
        force: Overwrite existing entry without error

    Returns:
        Path to local catalog file

    Raises:
        CatalogConfigError: If model exists and force=False
        SchemaVersionError: If schema version mismatch
    """
    local = load_local_catalog()
    local_models_raw = local.get("models")
    if not isinstance(local_models_raw, dict):
        local_models_raw = {}
        local["models"] = local_models_raw
    local_models: dict[str, object] = local_models_raw

    if model_id in local_models and not force:
        raise CatalogConfigError(
            f"Model '{model_id}' already exists in local catalog. Use force=True to overwrite."
        )

    local_models[model_id] = model_config
    return save_local_catalog(local)


def remove_model_from_local(model_id: str) -> Path | None:
    """
    Remove a model from local catalog.

    Args:
        model_id: Model ID to remove

    Returns:
        Path to local catalog file, or None if model wasn't present
    """
    local = load_local_catalog()
    local_models_raw = local.get("models")
    if not isinstance(local_models_raw, dict):
        return None

    if model_id not in local_models_raw:
        return None

    del local_models_raw[model_id]
    return save_local_catalog(local)


def list_local_models() -> list[str]:
    """
    List model IDs in local catalog.

    Returns:
        List of model IDs
    """
    try:
        local = load_local_catalog()
        models_raw = local.get("models")
        if isinstance(models_raw, dict):
            return list(models_raw.keys())
        return []
    except SchemaVersionError:
        return []


def merge_catalogs(
    static: dict[str, object],
    dynamic: dict[str, object],
) -> dict[str, object]:
    """
    Merge static and dynamic catalogs.

    Local models completely replace static models (no deep merge).
    Local transformations are added to static transformations.

    Args:
        static: Static catalog dictionary
        dynamic: Dynamic local catalog dictionary

    Returns:
        Merged catalog dictionary
    """
    static_transforms = static.get("transformations")
    static_models = static.get("models")

    result: dict[str, object] = {
        "catalog_version": static.get(
            "catalog_version", CURRENT_SCHEMA_VERSIONS[ConfigType.CATALOG]
        ),
        "catalog_type": "merged",
        "transformations": dict(static_transforms)
        if isinstance(static_transforms, dict)
        else {},
        "models": dict(static_models) if isinstance(static_models, dict) else {},
    }

    # Local transformations add/replace static
    dynamic_transforms = dynamic.get("transformations")
    if isinstance(dynamic_transforms, dict):
        transforms = result["transformations"]
        if isinstance(transforms, dict):
            transforms.update(dynamic_transforms)

    # Local models completely replace static (no merge)
    dynamic_models = dynamic.get("models")
    if isinstance(dynamic_models, dict):
        models = result["models"]
        if isinstance(models, dict):
            for model_id, model_config in dynamic_models.items():
                models[model_id] = model_config

    return result
