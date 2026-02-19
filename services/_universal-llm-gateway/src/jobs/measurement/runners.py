"""
CPU measurement test runner.

Synchronous function designed to run in executor for blocking I/O isolation.
Measures RAM requirements for model loading at specific context sizes.
"""

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
        return True


def _to_int(value: object) -> int:
    """Convert numpy/scalar types to native Python int for YAML serialization."""
    return int(value)  # type: ignore[arg-type]


def run_cpu_test(
    model_path: str,
    context: int,
    n_batch: int = 512,
    mmproj_path: str | None = None,
) -> dict[str, Any]:
    """Run CPU memory test in subprocess.

    Measures RAM requirements with no GPU offload (n_gpu_layers=0).
    Must be called via run_in_executor to avoid blocking async loop.

    Args:
        model_path: Path to GGUF model file.
        context: Context length to test.
        n_batch: Batch size for inference.
        mmproj_path: Optional path to mmproj/CLIP file for vision models.

    Returns:
        Profile dict with success, ram_mb, vram_mb, n_gpu_layers.
    """
    try:
        from inference_djinn.scripts.config_generators.gguf.measurement import (
            test_single_cpu_memory,
        )

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
            return {
                "success": True,
                "ram_mb": _to_int(ram),
                "vram_mb": 0,
                "n_gpu_layers": 0,
                "stderr": stderr,
            }
        return {"success": False, "stderr": stderr}
    except Exception as e:
        logger.error(f"CPU test failed: {e}")
        return {"success": False, "error": str(e)}
