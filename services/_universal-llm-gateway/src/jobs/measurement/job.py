"""
Measurement job for VRAM/RAM profiling.

Smart context detection:
- GPU mode: starts at training_context_length, steps down until it fits on GPU
- CPU mode: uses training_context_length
- Auto mode: tries GPU first with step-down, falls back to CPU
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, override

from universal_logging import get_logger

from ..context_detection import (
    get_step_down_contexts,
    get_training_context,
    resolve_model_path,
)
from ..job import Job
from .execution import (
    SubprocessTracker,
    apply_resource_caps,
    measure_auto_mode,
    measure_cpu_contexts,
    measure_gpu_with_stepdown,
)
from .helpers import (
    check_measurement_resources,
    update_catalog_with_results,
)
from .vllm import measure_vllm_contexts

if TYPE_CHECKING:
    from ...core.gateway_config import GatewayConfig

logger = get_logger(__name__)


def _lookup_catalog_entry(model_id: str) -> dict[str, Any] | None:
    """Fetch catalog entry for model (None if unavailable)."""
    try:
        from ...core.catalog import get_catalog_loader

        return get_catalog_loader().get_model(model_id)
    except Exception as e:
        logger.debug("Catalog lookup failed for '%s': %s", model_id, e)
        return None


@dataclass
class MeasureJobRequest:
    """Request parameters for measurement job."""

    model_id: str
    contexts: list[int] | None = None  # None = auto-detect from metadata
    mode: Literal["gpu", "cpu", "auto"] = "auto"
    n_batch: int = 512
    gpu_index: int = 0
    # Resource caps (None = use actual hardware capacity)
    vram_cap_mb: int | None = None  # Max VRAM for "fits" determination
    ram_cap_mb: int | None = None  # Max RAM for "fits" determination
    # Set by job when auto-detecting
    training_context_length: int | None = None
    # Hybrid mode: when full GPU offload fails, try partial offload
    enable_hybrid: bool = True
    # Hybrid safety margin: subtract N layers from max found (default: 2)
    safety_margin: int | None = None
    # Vision model support: path to mmproj/CLIP file
    mmproj_path: str | None = None
    # Catalog selection: True = update static catalog, False = update local catalog
    use_static_catalog: bool = False


@dataclass
class MeasurementJob(Job):
    """
    Measurement job for VRAM/RAM profiling.

    Runs measurement subprocess for each context size and updates catalog.
    Pre-loaded gateway configuration ensures no blocking I/O in async path.
    """

    request: MeasureJobRequest = field(
        default_factory=lambda: MeasureJobRequest(model_id="")
    )
    gateway_config: "GatewayConfig | None" = None
    job_type: str = field(default="measure", init=False)

    @override
    async def _run(self) -> None:
        """Execute measurement job."""
        if not self.gateway_config:
            raise RuntimeError("gateway_config must be provided at job construction")

        tracker = SubprocessTracker()
        self.emit_log(f"Starting measurement for {self.request.model_id}")
        self.emit_log(f"  Mode: {self.request.mode}")
        if self.request.vram_cap_mb:
            self.emit_log(f"  VRAM cap: {self.request.vram_cap_mb}MB")
        if self.request.ram_cap_mb:
            self.emit_log(f"  RAM cap: {self.request.ram_cap_mb}MB")

        # Show memory safety diagnostics
        from .execution import get_system_memory_info

        mem_info = get_system_memory_info()
        if mem_info.get("total_ram_mb"):
            self.emit_log(
                f"  System RAM: {mem_info['available_ram_mb']}MB available / "
                f"{mem_info['total_ram_mb']}MB total"
            )
            if mem_info.get("total_swap_mb"):
                self.emit_log(
                    f"  Swap: {mem_info['available_swap_mb']}MB available / "
                    f"{mem_info['total_swap_mb']}MB total"
                )
            self.emit_log(
                f"  Safety headroom: {mem_info['current_headroom_mb']}MB "
                f"(recommended: {mem_info['recommended_headroom_mb']}MB)"
            )
            if mem_info.get("safe_measurement_limit_mb", 0) > 0:
                self.emit_log(
                    f"  Safe probe limit: ~{mem_info['safe_measurement_limit_mb']}MB per subprocess"
                )

        # Show warnings if any
        for warning in mem_info.get("warnings", []):
            self.emit_log(f"  ⚠️  {warning}")

        try:
            # Check resources before attempting measurement
            can_proceed, error = await check_measurement_resources(
                self.request.model_id, self.gateway_config
            )
            if not can_proceed:
                self.emit_log(f"❌ Insufficient resources for measurement: {error}")
                raise RuntimeError(f"Insufficient resources: {error}")
            if error:
                self.emit_log(f"⚠️ {error}")

            model_path = await self._resolve_model_path()
            if not model_path:
                raise RuntimeError(f"Model not found: {self.request.model_id}")
            self.emit_log(f"  Model path: {model_path}")

            # Auto-detect contexts from model metadata if not specified
            if self.request.contexts is None:
                await self._detect_contexts_from_metadata()

            self.emit_log(f"  Contexts: {self.request.contexts}")

            # Detect engine type for dispatch
            entry = _lookup_catalog_entry(self.request.model_id)
            schema = (entry or {}).get("schema")
            loader_updates: dict[str, Any] | None = None
            if schema == "vllm":
                results, loader_updates = await self._measure_vllm(model_path, tracker)
            elif self.request.mode == "gpu":
                results = await measure_gpu_with_stepdown(
                    model_path,
                    self.request.contexts or [32768, 16384, 8192, 4096],
                    self.request.n_batch,
                    self.request.gpu_index,
                    self.request.mmproj_path,
                    self.request.enable_hybrid,
                    self.emit_log,
                    tracker,
                    self.request.safety_margin,
                )
            elif self.request.mode == "cpu":
                contexts = self._get_cpu_contexts()
                results = await measure_cpu_contexts(
                    model_path,
                    contexts,
                    self.request.n_batch,
                    self.request.mmproj_path,
                    self.emit_log,
                )
            else:
                results = await measure_auto_mode(
                    model_path,
                    self.request.contexts or [32768, 16384, 8192, 4096],
                    self.request.n_batch,
                    self.request.gpu_index,
                    self.request.mmproj_path,
                    self.request.enable_hybrid,
                    self.emit_log,
                    tracker,
                    self.request.safety_margin,
                )

            # Apply resource caps to determine which profiles "fit"
            apply_resource_caps(
                results,
                self.request.vram_cap_mb,
                self.request.ram_cap_mb,
                self.emit_log,
            )

            await update_catalog_with_results(
                self.request.model_id,
                self.request.mode,
                results,
                self.emit_log,
                use_static=self.request.use_static_catalog,
                loader_updates=loader_updates,
            )

            self.result = {"profiles": results}
            self.emit_log("Measurement complete")
        except asyncio.CancelledError:
            logger.info("MeasurementJob._run() received CancelledError, cleaning up...")
            self.emit_log("⚠️ Measurement cancelled, cleaning up subprocesses...")
            raise
        finally:
            logger.info(
                "MeasurementJob._run() finally block: calling tracker.kill_all()"
            )
            await tracker.kill_all()
            logger.info(
                "MeasurementJob._run() finally block: tracker.kill_all() completed"
            )

    def _get_cpu_contexts(self) -> list[int]:
        """Get context list for CPU measurement mode."""
        if self.request.contexts:
            return self.request.contexts
        elif self.request.training_context_length:
            return [self.request.training_context_length]
        else:
            return [8192, 4096, 2048]

    async def _detect_contexts_from_metadata(self) -> None:
        """
        Detect contexts from model's training_context_length.

        Raises RuntimeError if training context cannot be determined.
        """
        training_ctx = await get_training_context(self.request.model_id)
        self.request.training_context_length = training_ctx

        if training_ctx:
            self.emit_log(f"  Training context: {training_ctx}")
            self.request.contexts = get_step_down_contexts(training_ctx)
        else:
            # Training context is REQUIRED - fail with actionable message
            model_id = self.request.model_id

            # Check if model exists in catalog
            try:
                from ..core.catalog import get_catalog_loader

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
            except Exception:
                error_msg = (
                    f"Failed to determine training_context_length for {model_id}. "
                    "The model may not be in the catalog, or the catalog entry "
                    "may be missing required metadata."
                )

            self.emit_log(f"  ❌ {error_msg}")
            raise RuntimeError(error_msg)

    async def _measure_vllm(
        self, model_path: Path, tracker: SubprocessTracker
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Run vLLM-specific measurement (GPU only, no hybrid).

        Returns:
            Tuple of (profile_results, loader_updates_to_persist)
        """
        if self.request.mode == "cpu":
            raise RuntimeError("vLLM does not support CPU-only measurement")

        self.emit_log("  Engine: vLLM (GPU-only, no hybrid)")

        entry = _lookup_catalog_entry(self.request.model_id)
        model_format = (entry or {}).get("metadata", {}).get("format")
        loader_config = (entry or {}).get("loader", {})

        quantization = model_format if model_format in ("awq", "gptq") else None
        gpu_mem_util = loader_config.get("gpu_memory_utilization", 0.9)

        self.emit_log(f"  Quantization: {quantization or 'none'}")
        self.emit_log(f"  GPU memory utilization: {gpu_mem_util}")

        results = await measure_vllm_contexts(
            model_path,
            self.request.contexts or [32768, 16384, 8192, 4096],
            quantization,
            gpu_mem_util,
            self.emit_log,
            tracker,
        )

        loader_updates = {
            "gpu_memory_utilization": gpu_mem_util,
            "enforce_eager": True,
        }
        return results, loader_updates

    async def _resolve_model_path(self) -> Path | None:
        """Resolve model ID to file path (GGUF) or directory (vLLM)."""
        return resolve_model_path(self.request.model_id)
