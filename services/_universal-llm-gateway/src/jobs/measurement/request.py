"""Request parameters and catalog lookup helpers for measurement jobs."""

from dataclasses import dataclass
from typing import Any, Literal

from universal_logging import get_logger

logger = get_logger(__name__)


def lookup_catalog_entry(model_id: str) -> dict[str, Any] | None:
    """Fetch catalog entry for model (None if unavailable)."""
    try:
        from ...core.catalog import get_catalog_loader

        return get_catalog_loader().get_model(model_id)
    except Exception as e:
        logger.debug("Catalog lookup failed for '%s': %s", model_id, e)
        return None


@dataclass
class MeasureJobRequest:
    """Request parameters for measurement job.

    Fields:
        model_id: Catalog model identifier.
        contexts: Explicit context sizes to probe; None → auto-detected from
            training_context_length in the catalog or GGUF metadata.
        mode: Measurement mode — "gpu" (GPU only), "cpu" (CPU only), or "auto"
            (GPU with step-down fallback to CPU).
        n_batch: Batch size forwarded to the subprocess runner.
        gpu_index: Zero-based GPU device index.
        vram_cap_mb: Treat profiles exceeding this VRAM as not fitting. None =
            no cap (use actual hardware capacity).
        ram_cap_mb: Same cap semantics for RAM. None = uncapped.
        training_context_length: Populated by the job during auto-detection;
            not normally set by callers.
        enable_hybrid: When full GPU offload fails, retry with partial offload.
        safety_margin: Subtract this many layers from the discovered maximum
            when writing hybrid profiles (guards against OOM edge cases).
            None uses the runner default (2).
        mmproj_path: Absolute path to the mmproj/CLIP projection file for
            vision models. None for text-only models.
        use_static_catalog: True → write results to the static (shared) catalog;
            False → write to the local (per-node) catalog.
    """

    model_id: str
    contexts: list[int] | None = None
    mode: Literal["gpu", "cpu", "auto"] = "auto"
    n_batch: int = 512
    gpu_index: int = 0
    vram_cap_mb: int | None = None
    ram_cap_mb: int | None = None
    training_context_length: int | None = None
    enable_hybrid: bool = True
    safety_margin: int | None = None
    mmproj_path: str | None = None
    use_static_catalog: bool = False
