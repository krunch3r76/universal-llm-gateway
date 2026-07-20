"""Training context length lookup combining catalog metadata and GGUF fallback.

Prefers catalog metadata.training_context_length, then embedding limits, then
on-disk GGUF extraction when catalog entries are incomplete.
"""

from pathlib import Path

from universal_logging import get_logger

from .gguf_metadata import extract_training_context_from_gguf
from .path_resolution import resolve_model_path

logger = get_logger(__name__)


async def get_training_context(model_id: str) -> int | None:
    """
    Get training_context_length for a model.

    Priority:
    1. Catalog entry (authoritative)
    2. GGUF file metadata (fallback for GGUF models with incomplete catalog entries)
    3. None — measurement cannot proceed

    Args:
        model_id: Model identifier from catalog

    Returns:
        Training context length, or None if not found anywhere
    """
    potential_path = Path(model_id)
    if (
        potential_path.is_absolute()
        and potential_path.exists()
        and potential_path.is_file()
    ):
        logger.warning(
            f"Using absolute file path as model_id is deprecated: {model_id}. "
            "Use catalog ID instead."
        )
        return extract_training_context_from_gguf(potential_path)

    try:
        from ....core.catalog import get_catalog_loader

        loader = get_catalog_loader()
        model = loader.get_model(model_id)

        if not model:
            logger.warning(
                f"Model '{model_id}' not found in catalog. "
                "If recently added, restart gateway to reload catalog."
            )
        else:
            metadata = model.get("metadata", {})
            training_ctx = metadata.get("training_context_length")
            if training_ctx:
                return training_ctx
            max_ctx = (
                metadata.get("capabilities", {})
                .get("limits", {})
                .get("max_context_length")
            )
            if max_ctx:
                return max_ctx
            logger.warning(
                f"Catalog entry for '{model_id}' missing training_context_length "
                "and capabilities.limits.max_context_length; "
                "attempting GGUF extraction as fallback."
            )

    except Exception as e:
        logger.error(
            "Failed to access catalog for model '%s': %s", model_id, e, exc_info=True
        )

    model_path = resolve_model_path(model_id)
    if model_path and model_path.is_file() and model_path.suffix == ".gguf":
        ctx = extract_training_context_from_gguf(model_path)
        if ctx:
            logger.warning(
                f"Extracted training_context_length={ctx} from GGUF for '{model_id}'. "
                "Update catalog entry to avoid this fallback: "
                "metadata.training_context_length: %d",
                ctx,
            )
            return ctx

    logger.error(
        f"Cannot determine training_context_length for '{model_id}'. "
        "Update the catalog entry with the correct value."
    )
    return None
