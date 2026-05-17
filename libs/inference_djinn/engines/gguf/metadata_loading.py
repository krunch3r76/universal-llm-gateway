"""
GGUF metadata loading and caching utilities.

Owns GGUFReader import/patching, mtime-keyed metadata cache, and
load/clear behavior for inspector functions.
"""

import os
import time

from universal_logging import get_logger

from .gguf_metadata import GGUFMetadataLite

logger = get_logger(__name__)

# Metadata cache for inspector utility functions: {(model_path, mtime): GGUFMetadataLite}
_metadata_cache: dict[tuple[str, float], GGUFMetadataLite] = {}

# Apply GGUF patch to support newer quantization types before importing GGUFReader
try:
    from .gguf_patch import patch_gguf

    patch_gguf()
except Exception as e:
    logger.debug(f"Could not apply GGUF patch: {e}")

try:
    from gguf import GGUFReader

    gguf_reader_available = True
except ImportError:
    gguf_reader_available = False
    logger.error(
        "\033[1;5;91m[GGUF Inspector] GGUFReader import failed: 'gguf' package not available\033[0m"
    )


def load_gguf_metadata(
    model_path: str, use_cache: bool = True
) -> GGUFMetadataLite | None:
    """
    Load GGUF metadata from file with caching support.

    Args:
        model_path: Path to the GGUF model file
        use_cache: Whether to use cached metadata (default: True)

    Returns:
        GGUFMetadataLite object or None if not loadable
    """
    if not os.path.isfile(model_path):
        logger.debug(f"Model file not found: {model_path}")
        return None

    if not gguf_reader_available:
        logger.debug("GGUF reader not available")
        return None

    # Check cache first
    try:
        mtime = os.path.getmtime(model_path)
        cache_key = (model_path, mtime)

        if use_cache and cache_key in _metadata_cache:
            logger.debug(f"[GGUF Inspector] Using cached metadata for {model_path}")
            return _metadata_cache[cache_key]
    except Exception as e:
        logger.debug(f"Could not check cache for {model_path}: {e}")

    try:
        start_time = time.time()
        logger.debug(
            "\033[92m[GGUF Inspector] Initializing GGUFReader for model: %s\033[0m",
            model_path,
        )

        # Use GGUFReader with minimal overhead
        reader = GGUFReader(model_path)
        reader_init_time = time.time() - start_time
        logger.debug(
            f"[GGUF Inspector] GGUFReader initialized in {reader_init_time:.3f}s"
        )

        # Extract metadata
        extract_start = time.time()
        meta = GGUFMetadataLite.from_gguf(reader)
        extract_time = time.time() - extract_start

        total_time = time.time() - start_time
        logger.debug(
            f"[GGUF Inspector] Metadata extraction complete in {extract_time:.3f}s (total: {total_time:.3f}s)"
        )

        # Cache the result
        if use_cache:
            try:
                mtime = os.path.getmtime(model_path)
                cache_key = (model_path, mtime)
                _metadata_cache[cache_key] = meta
                logger.debug(f"[GGUF Inspector] Cached metadata for {model_path}")
            except Exception as e:
                logger.debug(f"Could not cache metadata for {model_path}: {e}")

        return meta
    except Exception as e:
        logger.debug(f"Could not load GGUF metadata from {model_path}: {e}")
        import traceback

        logger.debug(f"Traceback: {traceback.format_exc()}")
        return None


def clear_metadata_cache(model_path: str | None = None) -> None:
    """
    Clear metadata cache for a specific model or all models.

    Args:
        model_path: Path to model file to clear from cache, or None to clear all
    """
    global _metadata_cache

    if model_path is None:
        # Clear entire cache
        _metadata_cache.clear()
        logger.debug("[GGUF Inspector] Cleared entire metadata cache")
    else:
        # Clear specific model (all mtime entries)
        keys_to_remove = [key for key in _metadata_cache.keys() if key[0] == model_path]
        for key in keys_to_remove:
            del _metadata_cache[key]
        logger.debug(f"[GGUF Inspector] Cleared cache for {model_path}")
