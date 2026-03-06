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

from ...core.events import get_event_bus
from ...core.events.measurement import MeasurementEmbeddingDetected
from ..context_detection import (
    get_embedding_contexts,
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
from .gpu import run_layer_test
from .helpers import (
    check_measurement_resources,
    update_catalog_with_results,
)
from .vllm import find_min_gpu_utilization

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
                    f"  Safe probe limit: ~{mem_info['safe_measurement_limit_mb']}MB"
                    " per subprocess"
                )

        # Show warnings if any
        for warning in mem_info.get("warnings", []):
            self.emit_log(f"  ⚠️  {warning}")

        cleanup_completed = False
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
            is_embedding = (entry or {}).get("loader", {}).get("embedding") is True
            if schema == "vllm":
                results, loader_updates = await self._measure_vllm(
                    model_path, tracker, entry
                )
            elif schema == "llama-cpp" and is_embedding:
                results, loader_updates = await self._measure_gguf_embedding(
                    model_path, tracker, entry
                )
            elif schema == "llama-cpp":
                # Non-embedding GGUF text/vision models: use the standard
                # step-down path, which handles n_batch and layer probing correctly.
                if self.request.mode == "gpu":
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
                        self.request.gpu_index,
                        self.request.mmproj_path,
                        self.emit_log,
                        tracker,
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
                    self.request.gpu_index,
                    self.request.mmproj_path,
                    self.emit_log,
                    tracker,
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
            await tracker.kill_all()
            cleanup_completed = True
            raise
        finally:
            if not cleanup_completed:
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
        return (
            [self.request.training_context_length]
            if self.request.training_context_length
            else [8192, 4096, 2048]
        )

    async def _detect_contexts_from_metadata(self) -> None:
        """
        Detect contexts from model's training_context_length.

        Embedding models use get_embedding_contexts (2-point probe);
        text/vision models use get_step_down_contexts (full sweep).

        Raises RuntimeError if training context cannot be determined.
        """
        training_ctx = await get_training_context(self.request.model_id)
        self.request.training_context_length = training_ctx

        if training_ctx:
            self.emit_log(f"  Training context: {training_ctx}")
            entry = _lookup_catalog_entry(self.request.model_id)
            is_embedding = (entry or {}).get("loader", {}).get("embedding") is True
            if is_embedding:
                self.request.contexts = get_embedding_contexts(training_ctx)
            else:
                self.request.contexts = get_step_down_contexts(training_ctx)
        else:
            # Training context is REQUIRED - fail with actionable message
            model_id = self.request.model_id

            # Check if model exists in catalog
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
                self.emit_log(f"  ❌ {error_msg}")
                raise RuntimeError(error_msg) from e

            self.emit_log(f"  ❌ {error_msg}")
            raise RuntimeError(error_msg)

    async def _measure_vllm(
        self,
        model_path: Path,
        tracker: SubprocessTracker,
        entry: dict[str, Any] | None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Run vLLM-specific measurement (GPU only, no hybrid).

        Returns:
            Tuple of (profile_results, loader_updates_to_persist)
        """
        if self.request.mode == "cpu":
            raise RuntimeError("vLLM does not support CPU-only measurement")

        self.emit_log("  Engine: vLLM (GPU-only, no hybrid)")

        model_format = (entry or {}).get("metadata", {}).get("format")
        loader_config = (entry or {}).get("loader", {})

        quantization = model_format if model_format in ("awq", "gptq") else None

        self.emit_log(f"  Quantization: {quantization or 'none'}")

        is_embedding = (entry or {}).get("loader", {}).get("embedding") is True
        if is_embedding:
            ctx = self.request.training_context_length
            if not ctx:
                raise RuntimeError(
                    f"Embedding model '{self.request.model_id}' has no "
                    "training_context_length; cannot determine single probe size."
                )
            self.emit_log(
                f"  Embedding model: finding minimum gpu_memory_utilization "
                f"at context {ctx}"
            )
            await get_event_bus().publish_async_nowait(
                MeasurementEmbeddingDetected(
                    model_id=self.request.model_id, context_length=ctx
                )
            )
            gpu_mem_util, profile = await find_min_gpu_utilization(
                model_path,
                ctx,
                quantization,
                tracker,
                self.emit_log,
                loader_config=loader_config,
                device_index=self.request.gpu_index,
                is_embedding=True,
            )
            results: dict[str, dict[str, Any]] = {str(ctx): profile}
        else:
            contexts_to_measure = self.request.contexts or [32768, 16384, 8192, 4096]
            # Each context is probed independently for its minimum utilization.
            # Larger contexts need more KV cache → higher util; smaller ones
            # land lower, giving accurate per-context VRAM readings.
            # The largest context's found util is written to the catalog for
            # runtime use (serves all context sizes, so needs the highest floor).
            self.emit_log(
                "  Chat model: probing each context for minimum gpu_memory_utilization"
            )
            results: dict[str, dict[str, Any]] = {}
            gpu_mem_util: float | None = None
            for ctx in contexts_to_measure:
                self.emit_log(f"  Context {ctx}:")
                try:
                    util, profile = await find_min_gpu_utilization(
                        model_path,
                        ctx,
                        quantization,
                        tracker,
                        self.emit_log,
                        loader_config=loader_config,
                        device_index=self.request.gpu_index,
                        util_cap=0.95,
                    )
                    profile["gpu_memory_utilization"] = util
                    results[str(ctx)] = profile
                    if ctx == contexts_to_measure[0]:
                        # Largest context sets the runtime catalog value.
                        gpu_mem_util = util
                except RuntimeError as e:
                    self.emit_log(f"  ❌ {ctx}: {e}")
                    results[str(ctx)] = {"error": str(e)}
            if gpu_mem_util is None:
                logger.warning(
                    "All GPU measurements failed for '%s'; "
                    "falling back to gpu_memory_utilization=0.95",
                    self.request.model_id,
                )
                gpu_mem_util = 0.95

        loader_updates: dict[str, Any] = {
            "gpu_memory_utilization": gpu_mem_util,
            # Default for local catalog entries: disables CUDA graph capture,
            # which avoids long warm-up delays on first inference.
            "enforce_eager": True,
        }
        if is_embedding:
            loader_updates["embedding"] = True
            loader_updates["embedding_task_default"] = self._resolve_embedding_task_default(
                loader_config
            )
        return results, loader_updates

    async def _measure_gguf_embedding(
        self,
        model_path: Path,
        tracker: SubprocessTracker,
        entry: dict[str, Any] | None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Step-down GGUF embedding measurement.

        ∀ llama-cpp embedding model: KV cache scales with n_ctx even in
        embedding mode. Step down from training_context_length so each
        profile's VRAM/RAM reflects the actual cost at that context size.
        """
        loader_config = (entry or {}).get("loader", {})

        training_ctx = self.request.training_context_length
        contexts = self.request.contexts or (
            get_embedding_contexts(training_ctx) if training_ctx else None
        )
        if not contexts:
            raise RuntimeError(
                f"Embedding model '{self.request.model_id}' has no "
                "training_context_length; cannot determine contexts to probe."
            )

        n_layers = 0 if self.request.mode == "cpu" else -1
        mode_label = "CPU" if self.request.mode == "cpu" else "GPU"

        self.emit_log(
            f"  Engine: llama-cpp (embedding {mode_label}, contexts: {contexts})"
        )
        await get_event_bus().publish_async_nowait(
            MeasurementEmbeddingDetected(
                model_id=self.request.model_id, context_length=contexts[0]
            )
        )

        pooling = loader_config.get("pooling")
        ubatch_size = loader_config.get("ubatch_size")

        results: dict[str, dict[str, Any]] = {}
        for ctx in contexts:
            self.emit_log(f"  Probing context {ctx}...")
            # n_batch = ctx: matches runtime (per-profile sets n_batch = n_ctx)
            profile = await run_layer_test(
                model_path,
                n_layers=n_layers,
                context=ctx,
                n_batch=ctx,
                gpu_index=self.request.gpu_index,
                mmproj_path=None,
                tracker=tracker,
                embedding=True,
                pooling=pooling,
                ubatch_size=ubatch_size,
            )

            if profile.get("success"):
                profile["n_gpu_layers"] = n_layers
                vram = profile.get("vram_mb", "N/A")
                ram = profile.get("ram_mb", "N/A")
                self.emit_log(f"  ✅ {ctx}: VRAM={vram}MB, RAM={ram}MB")
                results[str(ctx)] = profile
            else:
                error = profile.get("error", "unknown")
                self.emit_log(f"  ❌ {ctx}: {error}")
                results[str(ctx)] = {"error": error}

        loader_updates: dict[str, Any] = {
            "embedding": True,
            "embedding_task_default": self._resolve_embedding_task_default(loader_config),
        }
        if pooling is not None:
            loader_updates["pooling"] = pooling
        if ubatch_size is not None:
            loader_updates["ubatch_size"] = ubatch_size
        return results, loader_updates

    def _resolve_embedding_task_default(self, loader_config: dict[str, Any]) -> str:
        """Return embedding_task_default from loader config, with fallback and warning."""
        task_default = loader_config.get("embedding_task_default")
        if task_default is None:
            logger.warning(
                "Embedding model '%s' missing loader.embedding_task_default; "
                "falling back to 'search_document'. "
                "Add embedding_task_default to the catalog loader config.",
                self.request.model_id,
            )
            return "search_document"
        return task_default

    async def _resolve_model_path(self) -> Path | None:
        """Resolve model ID to file path (GGUF) or directory (vLLM)."""
        return resolve_model_path(self.request.model_id)
