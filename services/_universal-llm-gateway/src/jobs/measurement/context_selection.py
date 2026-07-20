"""Context list selection and training-context auto-detection for measurement jobs."""

from collections.abc import Callable

from universal_logging import get_logger

from ..context_detection import (
    get_embedding_contexts,
    get_step_down_contexts,
    get_training_context,
)
from .request import MeasureJobRequest, lookup_catalog_entry

logger = get_logger(__name__)


def get_cpu_contexts(request: MeasureJobRequest) -> list[int]:
    """Get context list for CPU measurement mode."""
    if request.contexts:
        return request.contexts
    return (
        [request.training_context_length]
        if request.training_context_length
        else [8192, 4096, 2048]
    )


async def detect_contexts_from_metadata(
    request: MeasureJobRequest,
    emit_log: Callable[[str], None],
) -> None:
    """
    Detect contexts from model's training_context_length.

    Embedding models use get_embedding_contexts (2-point probe);
    text/vision models use get_step_down_contexts (full sweep).

    Raises RuntimeError if training context cannot be determined.
    """
    training_ctx = await get_training_context(request.model_id)
    request.training_context_length = training_ctx

    if training_ctx:
        emit_log(f"  Training context: {training_ctx}")
        entry = lookup_catalog_entry(request.model_id)
        is_embedding = (entry or {}).get("loader", {}).get("embedding") is True
        if is_embedding:
            request.contexts = get_embedding_contexts(training_ctx)
        else:
            request.contexts = get_step_down_contexts(training_ctx)
        return

    model_id = request.model_id

    try:
        from ...core.catalog import get_catalog_loader

        loader = get_catalog_loader()
        model = loader.get_model(model_id)

        if not model:
            error_msg = (
                f"Model '{model_id}' not found in catalog. "
                "If this model was recently added to the catalog, "
                "restart the gateway to reload the catalog:\n"
                "  systemctl --user restart super-universal-llm-gateway"
            )
        else:
            error_msg = (
                f"Catalog entry for '{model_id}' is missing "
                "required field 'metadata.training_context_length'. "
                "Update the catalog and restart the gateway."
            )
    except Exception as e:
        logger.warning(
            "Catalog access failed during context detection for '%s'",
            model_id,
            exc_info=True,
        )
        error_msg = (
            f"Failed to determine training_context_length for {model_id}. "
            "The model may not be in the catalog, or the catalog entry "
            "may be missing required metadata."
        )
        emit_log(f"  ❌ {error_msg}")
        raise RuntimeError(error_msg) from e

    emit_log(f"  ❌ {error_msg}")
    raise RuntimeError(error_msg)


def resolve_embedding_task_default(model_id: str, loader_config: dict) -> str:
    """Return embedding_task_default from loader config, with fallback and warning."""
    task_default = loader_config.get("embedding_task_default")
    if task_default is None:
        logger.warning(
            "Embedding model '%s' missing loader.embedding_task_default; "
            "falling back to 'search_document'. "
            "Add embedding_task_default to the catalog loader config.",
            model_id,
        )
        return "search_document"
    return task_default
