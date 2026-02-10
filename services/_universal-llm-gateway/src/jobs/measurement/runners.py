"""
GPU/CPU measurement test runners.

Synchronous functions designed to run in executor for blocking I/O isolation.
These measure VRAM/RAM requirements for model loading at specific context sizes.
"""

from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

HEADROOM_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB


def _has_ram_headroom(min_bytes: int) -> bool:
    """Check available RAM to avoid host OOM during measurement subprocess."""
    try:
        import psutil

        return psutil.virtual_memory().available >= min_bytes
    except Exception:
        # If psutil is unavailable or fails, do not block execution
        return True


def run_gpu_test(
    model_path: str,
    context: int,
    n_batch: int = 512,
    gpu_index: int = 0,
    mmproj_path: str | None = None,
) -> dict[str, Any]:
    """
    Run GPU layer test in subprocess.

    Measures VRAM/RAM with full GPU offload (n_gpu_layers=-1).
    Must be called via run_in_executor to avoid blocking async loop.

    Args:
        model_path: Path to GGUF model file
        context: Context length to test
        n_batch: Batch size for inference
        gpu_index: GPU device index
        mmproj_path: Optional path to mmproj/CLIP file for vision models

    Returns:
        Profile dict with success, ram_mb, vram_mb, n_gpu_layers
    """
    try:
        from inference_djinn.scripts.config_generators.gguf.testing import (
            test_single_gpu_layers,
        )
        from inference_djinn.scripts.config_generators.gguf.utils import to_native_int

        if not _has_ram_headroom(HEADROOM_BYTES):
            msg = "Insufficient RAM headroom (<4GiB) for GPU test"
            logger.warning(msg)
            return {"success": False, "error": msg}

        success, ram, vram = test_single_gpu_layers(
            model_path=model_path,
            n_layers=-1,
            n_ctx=context,
            n_batch=n_batch,
            gpu_index=gpu_index,
            mmproj_path=mmproj_path,
        )

        if success:
            # Convert numpy types to native Python ints for YAML serialization
            return {
                "success": True,
                "ram_mb": to_native_int(ram),
                "vram_mb": to_native_int(vram),
                "n_gpu_layers": -1,
            }
        logger.warning(f"GPU test returned success=False for {model_path}@{context}")
        return {"success": False, "error": "GPU test returned success=False"}
    except Exception as e:
        logger.warning(f"GPU test exception: {e}")
        return {"success": False, "error": str(e)}


def run_cpu_test(
    model_path: str,
    context: int,
    n_batch: int = 512,
    mmproj_path: str | None = None,
) -> dict[str, Any]:
    """
    Run CPU memory test in subprocess.

    Measures RAM requirements with no GPU offload (n_gpu_layers=0).
    Must be called via run_in_executor to avoid blocking async loop.

    Args:
        model_path: Path to GGUF model file
        context: Context length to test
        n_batch: Batch size for inference
        mmproj_path: Optional path to mmproj/CLIP file for vision models

    Returns:
        Profile dict with success, ram_mb, vram_mb, n_gpu_layers
    """
    try:
        from inference_djinn.scripts.config_generators.gguf.testing import (
            test_single_cpu_memory,
        )
        from inference_djinn.scripts.config_generators.gguf.utils import to_native_int

        if not _has_ram_headroom(HEADROOM_BYTES):
            msg = "Insufficient RAM headroom (<4GiB) for CPU test"
            logger.warning(msg)
            return {"success": False, "error": msg}

        success, ram, stderr = test_single_cpu_memory(
            model_path=model_path,
            n_ctx=context,
            n_batch=n_batch,
            mmproj_path=mmproj_path,
        )

        if success:
            # Convert numpy types to native Python ints for YAML serialization
            return {
                "success": True,
                "ram_mb": to_native_int(ram),
                "vram_mb": 0,
                "n_gpu_layers": 0,
                "stderr": stderr,
            }
        return {"success": False, "stderr": stderr}
    except Exception as e:
        logger.debug(f"CPU test failed: {e}")
        return {"success": False, "error": str(e)}


def run_hybrid_test(
    model_path: str,
    context: int,
    n_batch: int = 512,
    gpu_index: int = 0,
    mmproj_path: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
    min_layers_hint: int | None = None,
) -> dict[str, Any]:
    """
    Run hybrid (partial GPU offload) test using binary search.

    When full GPU offload fails (OOM), finds the maximum number of layers
    that fit on GPU via binary search. Remaining layers run on CPU.
    Must be called via run_in_executor to avoid blocking async loop.

    Args:
        model_path: Path to GGUF model file
        context: Context length to test
        n_batch: Batch size for inference
        gpu_index: GPU device index
        mmproj_path: Optional path to mmproj/CLIP file for vision models
        progress_callback: Optional thread-safe callback(message: str) for progress updates
        min_layers_hint: Minimum layers known to fit (from larger context), used as lower bound

    Returns:
        Profile dict with:
        - success: True if hybrid configuration found
        - ram_mb, vram_mb: Resource usage
        - n_gpu_layers: Actual layer count (not -1)
        - total_layers: Total layers in model (for logging)
    """
    try:
        from inference_djinn.scripts.config_generators.gguf.testing import (
            find_max_gpu_layers_binary_search,
            test_single_gpu_layers,
        )
        from inference_djinn.scripts.config_generators.gguf.utils import (
            extract_metadata,
            to_native_int,
        )

        if not _has_ram_headroom(HEADROOM_BYTES):
            msg = (
                "Insufficient RAM headroom (<4GiB) for hybrid test; "
                f"context={context}, n_batch={n_batch}"
            )
            logger.warning(msg)
            return {"success": False, "error": msg}

        # Get total layer count from model metadata
        meta, _ = extract_metadata(model_path)
        if meta and hasattr(meta, "block_count") and meta.block_count > 0:
            # Convert numpy type to native Python int
            total_layers = to_native_int(meta.block_count)
            # Fallback if conversion returns None or invalid value
            if total_layers is None or total_layers <= 0:
                total_layers = 120
                logger.warning(
                    (
                        "block_count conversion failed; "
                        "using heuristic total_layers=%s for %s"
                    ),
                    total_layers,
                    model_path,
                )
        else:
            # Fallback heuristic when block_count is missing in metadata
            total_layers = 120
            logger.warning(
                (
                    "block_count missing in metadata; "
                    "using heuristic total_layers=%s for %s"
                ),
                total_layers,
                model_path,
            )

        # Binary search for maximum layers that fit
        best_layers, ram, vram = find_max_gpu_layers_binary_search(
            model_path=model_path,
            n_ctx=context,
            n_batch=n_batch,
            gpu_index=gpu_index,
            max_layers_hint=total_layers,
            mmproj_path=mmproj_path,
            progress_callback=progress_callback,
            min_layers_hint=min_layers_hint,
        )

        if best_layers > 0 and ram is not None and vram is not None:
            # Apply -2 safety buffer for first hybrid context (at edge of fitting)
            # Note: min_layers_hint indicates if this is first (None) or subsequent (value)
            # For first hybrid (no hint), apply -2 and verify with actual measurement
            if min_layers_hint is None:
                buffered_layers = max(1, best_layers - 2)
                # Verify by actually measuring the reduced layer count
                success, verified_ram, verified_vram = test_single_gpu_layers(
                    model_path=model_path,
                    n_layers=buffered_layers,
                    n_ctx=context,
                    n_batch=n_batch,
                    gpu_index=gpu_index,
                    mmproj_path=mmproj_path,
                )
                if success and verified_ram is not None and verified_vram is not None:
                    # Use verified measurements (more accurate)
                    ram = verified_ram
                    vram = verified_vram
                else:
                    logger.warning(
                        "Verification at %d layers failed, using %d layer measurements",
                        buffered_layers,
                        best_layers,
                    )
            else:
                # Subsequent hybrid: no safety margin
                buffered_layers = best_layers

            # Convert all numpy types to native Python ints for YAML serialization
            return {
                "success": True,
                "ram_mb": to_native_int(ram),
                "vram_mb": to_native_int(vram),
                "n_gpu_layers": to_native_int(buffered_layers),
                "total_layers": to_native_int(total_layers),
            }
        return {
            "success": False,
            "error": "No valid hybrid configuration found",
        }
    except RuntimeError as e:
        # Context too large even for partial offload
        logger.warning(f"Hybrid test failed: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.warning(f"Hybrid test exception: {e}")
        return {"success": False, "error": str(e)}
