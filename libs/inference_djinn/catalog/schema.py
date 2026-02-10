"""
Schema versioning for dynamic local configuration files.

Provides version constants, validation, and migration guidance for:
  - Model catalogs ($HOME/.gateway/catalog.yaml)
  - Future local configs as needed

Schema Version Format: MAJOR.MINOR
  - MAJOR: Breaking changes requiring migration
  - MINOR: Backward-compatible additions

Version History:
  1.0 - Initial catalog schema (model_catalog.yaml format)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ConfigType(str, Enum):
    """Types of versioned configuration files."""

    CATALOG = "catalog"


# Current schema versions for each config type
CURRENT_SCHEMA_VERSIONS: dict[ConfigType, str] = {
    ConfigType.CATALOG: "1.0",
}


@dataclass
class SchemaValidationResult:
    """Result of schema version validation."""

    valid: bool
    config_type: ConfigType
    found_version: str | None
    expected_version: str
    message: str
    needs_migration: bool = False


def get_current_version(config_type: ConfigType) -> str:
    """Get the current schema version for a config type."""
    return CURRENT_SCHEMA_VERSIONS[config_type]


def parse_version(version_str: str) -> tuple[int, int]:
    """Parse version string into (major, minor) tuple."""
    try:
        parts = version_str.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor)
    except (ValueError, IndexError):
        return (0, 0)


def validate_catalog_schema(catalog: dict[str, Any]) -> SchemaValidationResult:
    """
    Validate catalog schema version.

    Args:
        catalog: Loaded catalog dictionary

    Returns:
        SchemaValidationResult with validation status and guidance
    """
    expected = CURRENT_SCHEMA_VERSIONS[ConfigType.CATALOG]
    found = catalog.get("catalog_version")

    if not found:
        return SchemaValidationResult(
            valid=False,
            config_type=ConfigType.CATALOG,
            found_version=None,
            expected_version=expected,
            message="Missing 'catalog_version' field. Add: catalog_version: '1.0'",
            needs_migration=True,
        )

    # Normalize version string
    found_str = str(found)
    expected_parsed = parse_version(expected)
    found_parsed = parse_version(found_str)

    # Check major version compatibility
    if found_parsed[0] > expected_parsed[0]:
        return SchemaValidationResult(
            valid=False,
            config_type=ConfigType.CATALOG,
            found_version=found_str,
            expected_version=expected,
            message=(
                f"Catalog version {found_str} is newer than supported {expected}. "
                f"Update inference_djinn to support this catalog version."
            ),
            needs_migration=False,
        )

    if found_parsed[0] < expected_parsed[0]:
        return SchemaValidationResult(
            valid=False,
            config_type=ConfigType.CATALOG,
            found_version=found_str,
            expected_version=expected,
            message=(
                f"Catalog version {found_str} is outdated. "
                f"Run migration: python -m inference_djinn.catalog.migrate --to {expected}"
            ),
            needs_migration=True,
        )

    # Major versions match - compatible
    return SchemaValidationResult(
        valid=True,
        config_type=ConfigType.CATALOG,
        found_version=found_str,
        expected_version=expected,
        message=f"Catalog schema version {found_str} is compatible",
        needs_migration=False,
    )


def get_catalog_template() -> dict[str, Any]:
    """
    Get a template for a new dynamic catalog with correct schema version.

    Returns:
        Dictionary with required catalog structure
    """
    return {
        "catalog_version": CURRENT_SCHEMA_VERSIONS[ConfigType.CATALOG],
        "catalog_type": "dynamic",
        "transformations": {},
        "models": {},
    }


def ensure_catalog_version(catalog: dict[str, Any]) -> dict[str, Any]:
    """
    Ensure catalog has a version field, adding current version if missing.

    Args:
        catalog: Catalog dictionary (modified in place)

    Returns:
        The catalog dictionary with version ensured
    """
    if "catalog_version" not in catalog:
        catalog["catalog_version"] = CURRENT_SCHEMA_VERSIONS[ConfigType.CATALOG]
    return catalog


def validate_activated_contexts(
    model_id: str, model_entry: dict[str, Any]
) -> list[str]:
    """
    Validate activated_gpu_contexts and activated_cpu_contexts fields.

    Checks that:
    - Values are lists of integers
    - Context values match existing profile keys

    Args:
        model_id: Model identifier (for error messages)
        model_entry: Model entry dictionary

    Returns:
        List of validation error messages (empty if valid)
    """
    errors: list[str] = []
    metadata = model_entry.get("metadata", {})
    configurations = model_entry.get("configurations", {})

    # Collect all valid profile contexts
    valid_gpu_contexts: set[int] = set()
    valid_cpu_contexts: set[int] = set()

    for config_name, config in configurations.items():
        if not isinstance(config, dict):
            continue  # Skip aliases and reserved keys
        profiles = config.get("profiles", {})
        for ctx_str in profiles.keys():
            try:
                ctx = int(ctx_str)
                if "cpu" in config_name.lower():
                    valid_cpu_contexts.add(ctx)
                else:
                    valid_gpu_contexts.add(ctx)
            except ValueError:
                pass

    # Validate activated_gpu_contexts
    activated_gpu = metadata.get("activated_gpu_contexts")
    if activated_gpu is not None:
        if not isinstance(activated_gpu, list):
            errors.append(
                f"{model_id}: activated_gpu_contexts must be a list, got {type(activated_gpu).__name__}"
            )
        else:
            for ctx in activated_gpu:
                if not isinstance(ctx, int):
                    errors.append(
                        f"{model_id}: activated_gpu_contexts values must be integers, got {type(ctx).__name__}"
                    )
                elif valid_gpu_contexts and ctx not in valid_gpu_contexts:
                    errors.append(
                        f"{model_id}: activated_gpu_contexts value {ctx} not in profiles {sorted(valid_gpu_contexts)}"
                    )

    # Validate activated_cpu_contexts
    activated_cpu = metadata.get("activated_cpu_contexts")
    if activated_cpu is not None:
        if not isinstance(activated_cpu, list):
            errors.append(
                f"{model_id}: activated_cpu_contexts must be a list, got {type(activated_cpu).__name__}"
            )
        else:
            for ctx in activated_cpu:
                if not isinstance(ctx, int):
                    errors.append(
                        f"{model_id}: activated_cpu_contexts values must be integers, got {type(ctx).__name__}"
                    )
                elif valid_cpu_contexts and ctx not in valid_cpu_contexts:
                    errors.append(
                        f"{model_id}: activated_cpu_contexts value {ctx} not in cpu_profiles {sorted(valid_cpu_contexts)}"
                    )

    return errors


def validate_catalog_models(catalog: dict[str, Any]) -> list[str]:
    """
    Validate all models in a catalog for activated contexts.

    Args:
        catalog: Catalog dictionary

    Returns:
        List of all validation error messages
    """
    all_errors: list[str] = []
    models = catalog.get("models", {})

    for model_id, model_entry in models.items():
        if isinstance(model_entry, dict):
            errors = validate_activated_contexts(model_id, model_entry)
            all_errors.extend(errors)

    return all_errors
