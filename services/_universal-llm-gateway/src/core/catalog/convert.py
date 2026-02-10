"""
Catalog Conversion - Schema-driven conversion to registry format.

V2 Architecture:
    - Conversion logic delegated to engine schemas
    - This module provides the public API; schemas do the work
    - Fail-fast on invalid entries (no silent exclusions)

Invariants:
    ∀ model: convert(model) ⟺ schema.convert(model)
        where schema = SchemaRegistry.get_for_model(model)
    ∀ model: model.schema = None ⟹ conversion fails (logged + excluded)
    ∀ model: schema.convert() = None ⟹ no valid profiles (logged + excluded)

V2 Breaking Changes:
    - Schema field REQUIRED (no format-based derivation)
    - Models without profiles are EXCLUDED from converted output (logged)
    - No V1 config name handling (gpu-batch512, vllm-default, etc.)
"""

from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

from .schemas import SchemaRegistry

logger = get_logger(__name__)


def to_model_loaders_format(
    model_id: str,
    get_model_fn: Callable[[str], dict[str, Any] | None],
) -> dict[str, Any] | None:
    """
    Convert catalog model entry to registry format.

    Delegates to the appropriate engine schema for conversion.
    The schema handles all engine-specific logic (loader params,
    profile structure, resource mapping).

    Args:
        model_id: Model identifier
        get_model_fn: Function to retrieve model entry by ID

    Returns:
        Model entry in registry format, or None if:
        - Model not found
        - Schema field missing
        - No schema matches the model
        - Schema conversion fails (no valid profiles)

    Behavior:
        - Missing schema field: WARNING logged, model excluded
        - Unknown schema: WARNING logged, model excluded
        - No profiles: WARNING logged, model excluded
        - Conversion error: ERROR logged, model excluded

    Example:
        >>> entry = to_model_loaders_format("qwen3-32b-awq", catalog.get_model)
        >>> entry["info"]["engine"]
        'vllm'
        >>> entry["profiles"]["16384"]["loader"]["max_model_len"]
        16384
    """
    entry = get_model_fn(model_id)
    if not entry:
        logger.debug(f"Model '{model_id}' not found in catalog")
        return None

    # V2: Schema field REQUIRED (no fallback)
    schema_name = entry.get("schema")
    if not schema_name:
        logger.warning(
            f"Model '{model_id}' missing 'schema' field (V2 required) - "
            "EXCLUDED from registry"
        )
        return None

    # Get schema
    schema = SchemaRegistry.get_for_model(entry)
    if not schema:
        logger.warning(
            f"Model '{model_id}' has unknown schema '{schema_name}' - "
            "EXCLUDED from registry"
        )
        return None

    # Convert via schema
    try:
        converted = schema.convert(model_id, entry)
        if converted is None:
            logger.warning(
                f"Model '{model_id}' has no valid profiles "
                f"(schema={schema.engine}) - EXCLUDED from registry"
            )
            return None

        result: dict[str, Any] = {
            "info": converted.info,
            "base_loader": converted.base_loader,
            "profiles": converted.profiles,
        }

        if converted.cpu_profiles:
            result["cpu_profiles"] = converted.cpu_profiles

        return result

    except Exception as e:
        logger.error(
            f"Failed to convert model '{model_id}' (schema={schema.engine}): {e}",
            exc_info=True,
        )
        return None


def get_all_models_as_loaders_format(
    catalog: dict[str, Any],
    get_model_fn: Callable[[str], dict[str, Any] | None],
) -> dict[str, Any]:
    """
    Convert entire catalog to registry format.

    Args:
        catalog: Catalog dictionary with models
        get_model_fn: Function to retrieve model by ID

    Returns:
        Dictionary with 'models' key containing all converted models.
        Models that fail conversion are excluded (logged as warnings/errors).

    Note:
        Startup will fail if ALL models are excluded (empty registry).
    """
    models: dict[str, Any] = {}
    catalog_models = catalog.get("models", {})

    total_count = len(catalog_models)
    for model_id in catalog_models:
        loader_format = to_model_loaders_format(model_id, get_model_fn)
        if loader_format:
            models[model_id] = loader_format

    excluded_count = total_count - len(models)
    if excluded_count > 0:
        logger.warning(
            f"Excluded {excluded_count}/{total_count} models during conversion "
            f"(missing schema, no profiles, or conversion errors)"
        )

    if not models and total_count > 0:
        logger.error(
            f"CRITICAL: All {total_count} models excluded during conversion! "
            "Check catalog for missing schema fields or validation errors."
        )

    return {"models": models}
