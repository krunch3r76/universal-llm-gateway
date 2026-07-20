"""Main measurement run loop with engine dispatch and subprocess lifecycle."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..context_detection import resolve_model_path
from .context_selection import detect_contexts_from_metadata, get_cpu_contexts
from .embedding_measurement import measure_gguf_embedding_profiles
from .execution import (
    SubprocessTracker,
    apply_resource_caps,
    get_system_memory_info,
    measure_auto_mode,
    measure_cpu_contexts,
    measure_gpu_with_stepdown,
)
from .helpers import check_measurement_resources, update_catalog_with_results
from .request import MeasureJobRequest, lookup_catalog_entry
from .vllm_measurement import measure_vllm_profiles

if TYPE_CHECKING:
    from ...core.gateway_config import GatewayConfig

logger = get_logger(__name__)


def _log_memory_diagnostics(emit_log: Callable[[str], None]) -> None:
    """Emit system memory headroom diagnostics before measurement probes."""
    mem_info = get_system_memory_info()
    if mem_info.get("total_ram_mb"):
        emit_log(
            f"  System RAM: {mem_info['available_ram_mb']}MB available / "
            f"{mem_info['total_ram_mb']}MB total"
        )
        if mem_info.get("total_swap_mb"):
            emit_log(
                f"  Swap: {mem_info['available_swap_mb']}MB available / "
                f"{mem_info['total_swap_mb']}MB total"
            )
        emit_log(
            f"  Safety headroom: {mem_info['current_headroom_mb']}MB "
            f"(recommended: {mem_info['recommended_headroom_mb']}MB)"
        )
        if mem_info.get("safe_measurement_limit_mb", 0) > 0:
            emit_log(
                f"  Safe probe limit: ~{mem_info['safe_measurement_limit_mb']}MB"
                " per subprocess"
            )

    for warning in mem_info.get("warnings", []):
        emit_log(f"  ⚠️  {warning}")


async def _dispatch_measurement(
    request: MeasureJobRequest,
    model_path: Path,
    tracker: SubprocessTracker,
    emit_log: Callable[[str], None],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    """Select engine-specific measurement path and return profiles + loader updates."""
    entry = lookup_catalog_entry(request.model_id)
    schema = (entry or {}).get("schema")
    loader_updates: dict[str, Any] | None = None
    is_embedding = (entry or {}).get("loader", {}).get("embedding") is True

    if schema == "vllm":
        results, loader_updates = await measure_vllm_profiles(
            request, model_path, tracker, entry, emit_log
        )
    elif schema == "llama-cpp" and is_embedding:
        results, loader_updates = await measure_gguf_embedding_profiles(
            request, model_path, tracker, entry, emit_log
        )
    elif schema == "llama-cpp":
        results = await _measure_llama_cpp_text(request, model_path, tracker, emit_log)
    elif request.mode == "gpu":
        results = await measure_gpu_with_stepdown(
            model_path,
            request.contexts or [32768, 16384, 8192, 4096],
            request.n_batch,
            request.gpu_index,
            request.mmproj_path,
            request.enable_hybrid,
            emit_log,
            tracker,
            request.safety_margin,
        )
    elif request.mode == "cpu":
        contexts = get_cpu_contexts(request)
        results = await measure_cpu_contexts(
            model_path,
            contexts,
            request.n_batch,
            request.gpu_index,
            request.mmproj_path,
            emit_log,
            tracker,
        )
    else:
        results = await measure_auto_mode(
            model_path,
            request.contexts or [32768, 16384, 8192, 4096],
            request.n_batch,
            request.gpu_index,
            request.mmproj_path,
            request.enable_hybrid,
            emit_log,
            tracker,
            request.safety_margin,
        )

    return results, loader_updates


async def _measure_llama_cpp_text(
    request: MeasureJobRequest,
    model_path: Path,
    tracker: SubprocessTracker,
    emit_log: Callable[[str], None],
) -> dict[str, dict[str, Any]]:
    """Measure non-embedding GGUF text/vision models via step-down or CPU paths."""
    if request.mode == "gpu":
        return await measure_gpu_with_stepdown(
            model_path,
            request.contexts or [32768, 16384, 8192, 4096],
            request.n_batch,
            request.gpu_index,
            request.mmproj_path,
            request.enable_hybrid,
            emit_log,
            tracker,
            request.safety_margin,
        )
    if request.mode == "cpu":
        contexts = get_cpu_contexts(request)
        return await measure_cpu_contexts(
            model_path,
            contexts,
            request.n_batch,
            request.gpu_index,
            request.mmproj_path,
            emit_log,
            tracker,
        )
    return await measure_auto_mode(
        model_path,
        request.contexts or [32768, 16384, 8192, 4096],
        request.n_batch,
        request.gpu_index,
        request.mmproj_path,
        request.enable_hybrid,
        emit_log,
        tracker,
        request.safety_margin,
    )


async def run_measurement_job(
    request: MeasureJobRequest,
    gateway_config: "GatewayConfig",
    emit_log: Callable[[str], None],
    set_result: Callable[[dict[str, Any]], None],
) -> None:
    """Execute a full measurement job run with cleanup on cancel or failure."""
    tracker = SubprocessTracker()
    emit_log(f"Starting measurement for {request.model_id}")
    emit_log(f"  Mode: {request.mode}")
    if request.vram_cap_mb:
        emit_log(f"  VRAM cap: {request.vram_cap_mb}MB")
    if request.ram_cap_mb:
        emit_log(f"  RAM cap: {request.ram_cap_mb}MB")

    _log_memory_diagnostics(emit_log)

    cleanup_completed = False
    try:
        can_proceed, error = await check_measurement_resources(
            request.model_id, gateway_config
        )
        if not can_proceed:
            emit_log(f"❌ Insufficient resources for measurement: {error}")
            raise RuntimeError(f"Insufficient resources: {error}")
        if error:
            emit_log(f"⚠️ {error}")

        model_path = await resolve_model_path(request.model_id)
        if not model_path:
            raise RuntimeError(f"Model not found: {request.model_id}")
        emit_log(f"  Model path: {model_path}")

        if request.contexts is None:
            await detect_contexts_from_metadata(request, emit_log)

        emit_log(f"  Contexts: {request.contexts}")

        results, loader_updates = await _dispatch_measurement(
            request, model_path, tracker, emit_log
        )

        apply_resource_caps(
            results,
            request.vram_cap_mb,
            request.ram_cap_mb,
            emit_log,
        )

        await update_catalog_with_results(
            request.model_id,
            request.mode,
            results,
            emit_log,
            use_static=request.use_static_catalog,
            loader_updates=loader_updates,
        )

        set_result({"profiles": results})
        emit_log("Measurement complete")
    except asyncio.CancelledError:
        logger.info("MeasurementJob._run() received CancelledError, cleaning up...")
        emit_log("⚠️ Measurement cancelled, cleaning up subprocesses...")
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
