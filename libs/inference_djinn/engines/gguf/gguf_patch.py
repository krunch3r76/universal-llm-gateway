"""
Monkey-patch for gguf library to support newer quantization types.

This module extends GGMLQuantizationType to include newer quantization formats
that are not yet in the released gguf-py library.

Usage:
    Import this module before using gguf.GGUFReader:

    from inference_djinn.engines.gguf.gguf_patch import patch_gguf
    patch_gguf()

    from gguf import GGUFReader
    reader = GGUFReader('model.gguf')
"""

from universal_logging import get_logger
from enum import IntEnum

logger = get_logger(__name__)

# Track if patch has been applied
_patched = False


def patch_gguf() -> bool:
    """
    Monkey-patch gguf library to support newer quantization types.

    Returns:
        True if patch was applied, False if already patched or failed
    """
    global _patched

    if _patched:
        logger.debug("GGUF library already patched")
        return False

    try:
        import gguf.constants as constants
        from gguf.constants import GGML_QUANT_SIZES, GGMLQuantizationType

        # Get current max value
        current_max = max(qt.value for qt in GGMLQuantizationType)

        # Define new quantization types that may not be in the current release
        # Source: https://github.com/ggerganov/llama.cpp/blob/master/gguf-py/gguf/constants.py
        NEW_TYPES = {
            36: "Q4_0_4_4",
            37: "Q4_0_4_8",
            38: "Q4_0_8_8",
            39: "MXFP4",  # Microsoft MX Float Point 4-bit
        }

        # Define block sizes and type sizes for new types
        # Format: (block_size, type_size)
        # MXFP4: 32 elements per block, ~4 bits per element = 16 bytes per block
        NEW_QUANT_SIZES = {
            36: (32, 18),  # Q4_0_4_4
            37: (32, 18),  # Q4_0_4_8
            38: (32, 18),  # Q4_0_8_8
            39: (32, 16),  # MXFP4
        }

        # Create extended enum with all types
        extended_values = {}

        # Copy existing types
        for qt in GGMLQuantizationType:
            extended_values[qt.name] = qt.value

        # Add new types that don't exist
        for value, name in NEW_TYPES.items():
            if value > current_max:
                extended_values[name] = value
                logger.debug(f"Adding quantization type: {name} = {value}")

        # Create new enum class
        ExtendedGGMLQuantizationType = IntEnum("GGMLQuantizationType", extended_values)

        # Replace the enum in the constants module
        constants.GGMLQuantizationType = ExtendedGGMLQuantizationType

        # Add new entries to GGML_QUANT_SIZES dictionary
        for qt_value, (block_size, type_size) in NEW_QUANT_SIZES.items():
            if qt_value > current_max:
                # Get the enum instance
                qt_enum = ExtendedGGMLQuantizationType(qt_value)
                GGML_QUANT_SIZES[qt_enum] = (block_size, type_size)
                logger.debug(
                    f"Added GGML_QUANT_SIZES[{qt_enum.name}] = ({block_size}, {type_size})"
                )

        # Also replace in gguf_reader module if it's already imported
        try:
            import gguf.gguf_reader as gguf_reader

            if hasattr(gguf_reader, "GGMLQuantizationType"):
                gguf_reader.GGMLQuantizationType = ExtendedGGMLQuantizationType
                logger.debug("Patched gguf_reader.GGMLQuantizationType")
            if hasattr(gguf_reader, "GGML_QUANT_SIZES"):
                gguf_reader.GGML_QUANT_SIZES = GGML_QUANT_SIZES
                logger.debug("Patched gguf_reader.GGML_QUANT_SIZES")
        except ImportError:
            pass

        _patched = True
        logger.info(
            f"Successfully patched GGUF library to support quantization types up to {max(extended_values.values())}"
        )

        return True

    except Exception as e:
        logger.error(f"Failed to patch GGUF library: {e}")
        import traceback

        logger.debug(traceback.format_exc())
        return False


def get_quantization_type_name(type_id: int) -> str | None:
    """
    Get the name of a quantization type by its ID.

    Args:
        type_id: Quantization type ID

    Returns:
        Name of the quantization type, or None if unknown
    """
    try:
        from gguf.constants import GGMLQuantizationType

        for qt in GGMLQuantizationType:
            if qt.value == type_id:
                return qt.name
    except Exception:
        pass

    # Fallback for known types not in enum
    KNOWN_TYPES = {
        36: "Q4_0_4_4",
        37: "Q4_0_4_8",
        38: "Q4_0_8_8",
        39: "MXFP4",
    }

    return KNOWN_TYPES.get(type_id, f"UNKNOWN_{type_id}")
