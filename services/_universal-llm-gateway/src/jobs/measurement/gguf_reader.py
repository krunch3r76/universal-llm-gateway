"""
Minimal GGUF binary header reader for measurement.

Reads KV metadata directly from the GGUF binary format using only stdlib
(struct). No dependency on gguf-py or gguf_patch (deprecated llama-cpp-python
engine path).

Spec: https://github.com/ggerganov/ggml/blob/master/docs/gguf.md
"""

import struct
from pathlib import Path
from typing import BinaryIO

from universal_logging import get_logger

logger = get_logger(__name__)

_GGUF_MAGIC = b"GGUF"
_GGUF_SUPPORTED_VERSIONS = {2, 3}

# gguf_metadata_value_type → (struct format, byte size)
# Types 8 (string) and 9 (array) require variable-length handling.
_GGUF_SCALAR_TYPES: dict[int, tuple[str, int]] = {
    0: ("<B", 1),   # UINT8
    1: ("<b", 1),   # INT8
    2: ("<H", 2),   # UINT16
    3: ("<h", 2),   # INT16
    4: ("<I", 4),   # UINT32
    5: ("<i", 4),   # INT32
    6: ("<f", 4),   # FLOAT32
    7: ("<B", 1),   # BOOL
    10: ("<Q", 8),  # UINT64
    11: ("<q", 8),  # INT64
    12: ("<d", 8),  # FLOAT64
}  # fmt: skip

# block_count may be stored as uint32 (4) or uint64 (10) per spec
_BLOCK_COUNT_TYPES = {4, 10}


def _skip_value(f: BinaryIO, value_type: int) -> None:
    """Advance file position past one GGUF KV value."""
    if value_type in _GGUF_SCALAR_TYPES:
        _, size = _GGUF_SCALAR_TYPES[value_type]
        f.read(size)
    elif value_type == 8:  # string
        (slen,) = struct.unpack("<Q", f.read(8))
        f.read(slen)
    elif value_type == 9:  # array
        (arr_type,) = struct.unpack("<I", f.read(4))
        (arr_count,) = struct.unpack("<Q", f.read(8))
        for _ in range(arr_count):
            _skip_value(f, arr_type)
    else:
        raise ValueError(f"Unknown GGUF value type: {value_type}")


def _read_scalar(f: BinaryIO, value_type: int) -> int:
    """Read a scalar integer value from the current file position."""
    fmt, size = _GGUF_SCALAR_TYPES[value_type]
    (value,) = struct.unpack(fmt, f.read(size))
    return int(value)


def extract_block_count(model_path: Path) -> int | None:
    """Extract block_count (total layer count) from GGUF binary header.

    Scans KV metadata for the `[arch].block_count` key.
    Early-terminates on first match.

    Args:
        model_path: Path to GGUF model file.

    Returns:
        Layer count, or None if extraction fails.
    """
    try:
        with open(model_path, "rb") as f:
            if f.read(4) != _GGUF_MAGIC:
                logger.warning("Not a GGUF file: %s", model_path)
                return None

            (version,) = struct.unpack("<I", f.read(4))
            if version not in _GGUF_SUPPORTED_VERSIONS:
                logger.warning("Unsupported GGUF version %d in %s", version, model_path)
                return None

            f.read(8)  # tensor_count (uint64)
            (kv_count,) = struct.unpack("<Q", f.read(8))

            for _ in range(kv_count):
                (key_len,) = struct.unpack("<Q", f.read(8))
                key = f.read(key_len).decode("utf-8")
                (value_type,) = struct.unpack("<I", f.read(4))

                if key.endswith(".block_count") and value_type in _BLOCK_COUNT_TYPES:
                    count = _read_scalar(f, value_type)
                    return count if count > 0 else None

                _skip_value(f, value_type)

    except Exception as e:
        logger.warning("Failed to extract block_count from %s: %s", model_path, e)
    return None
