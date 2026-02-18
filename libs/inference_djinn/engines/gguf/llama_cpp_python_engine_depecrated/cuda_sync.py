"""
CUDA Synchronization Utilities for GGUF Engine

Provides proper CUDA synchronization between warmup and generation calls
to prevent GGML memory pool corruption.

Background:
-----------
GGML's CUDA operations run asynchronously. When a warmup call returns to Python,
CUDA kernels may still be running, and GGML's memory pool bookkeeping may still
be updating. If generation starts immediately, it can try to allocate from an
inconsistent pool, causing crashes:

    GGML_ASSERT(ptr == (void *) ((char *)(pool_addr) + pool_used)) failed

Solution:
---------
Use llama.cpp's native `llama_synchronize(ctx)` to wait for all GGML CUDA
operations to complete before starting generation. This is context-specific
and won't interfere with other processes.

No Fallback:
------------
We do NOT use cudaDeviceSynchronize() as a fallback because it's a device-wide
operation that would synchronize ALL CUDA streams on the entire GPU, potentially
interfering with other processes or concurrent operations.
"""

from universal_logging import get_logger
import time
from typing import Any

logger = get_logger(__name__)


class CUDASynchronizer:
    """
    Handles CUDA synchronization between GGML operations.

    Uses llama.cpp's native context-specific synchronization only.
    """

    def __init__(self):
        """Initialize CUDA synchronization with method detection."""
        self._llama_sync_available = False
        self._llama_cpp = None

        # Try to import llama_cpp for native synchronization
        try:
            from llama_cpp import llama_cpp

            if hasattr(llama_cpp, "llama_synchronize"):
                self._llama_cpp = llama_cpp
                self._llama_sync_available = True
                logger.info("✅ llama_synchronize available (native GGML context sync)")
            else:
                logger.warning("❌ llama_synchronize not found in llama_cpp")
                logger.warning(
                    "   GGML CUDA sync unavailable - KV warmup may cause crashes"
                )
        except ImportError as e:
            logger.warning(f"❌ Could not import llama_cpp: {e}")
            logger.warning(
                "   GGML CUDA sync unavailable - KV warmup may cause crashes"
            )

    def synchronize(self, llama_model: Any) -> float | None:
        """
        Synchronize GGML CUDA state to ensure all operations are complete.

        This should be called after warmup and before generation to prevent
        GGML memory pool corruption. Uses llama.cpp's context-specific
        synchronization, which won't interfere with other processes.

        Args:
            llama_model: llama_cpp.Llama instance with .ctx attribute

        Returns:
            Synchronization time in milliseconds, or None if sync unavailable
        """
        if not self._llama_sync_available:
            logger.debug("⚠️  llama_synchronize unavailable - proceeding without sync")
            return None

        try:
            if not hasattr(llama_model, "ctx") or llama_model.ctx is None:
                logger.error("❌ Llama model has no valid .ctx attribute")
                return None

            start = time.perf_counter()
            self._llama_cpp.llama_synchronize(llama_model.ctx)
            elapsed_ms = (time.perf_counter() - start) * 1000

            logger.debug(f"✅ GGML CUDA synchronized ({elapsed_ms:.3f}ms)")
            return elapsed_ms

        except Exception as e:
            logger.error(f"❌ llama_synchronize failed: {e}")
            return None

    @property
    def available(self) -> bool:
        """Check if synchronization is available."""
        return self._llama_sync_available

    @property
    def method(self) -> str:
        """Get the name of the synchronization method."""
        return "llama_synchronize" if self._llama_sync_available else "none"


# Global singleton instance
_synchronizer: CUDASynchronizer | None = None


def get_synchronizer() -> CUDASynchronizer:
    """
    Get global CUDA synchronizer instance (singleton).

    Returns:
        CUDASynchronizer instance
    """
    global _synchronizer
    if _synchronizer is None:
        _synchronizer = CUDASynchronizer()
    return _synchronizer


def synchronize_cuda(llama_model: Any) -> float | None:
    """
    Convenience function to synchronize CUDA state.

    Args:
        llama_model: llama_cpp.Llama instance

    Returns:
        Synchronization time in milliseconds, or None if sync failed
    """
    return get_synchronizer().synchronize(llama_model)
