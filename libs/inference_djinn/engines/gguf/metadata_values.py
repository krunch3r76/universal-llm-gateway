"""
GGUF metadata value normalization utilities.

Normalizes GGUF metadata scalar/bytes/numpy values into safe strings
for downstream heuristics in model type detection and config generation.
"""

from __future__ import annotations


def _safe_numpy_to_string(value, default: str = "unknown") -> str:
    """Safely convert numpy types, bytes, and non-string values to strings."""
    if value is None:
        return default

    import numpy as np

    # Handle numpy array types that need conversion
    if hasattr(value, "tobytes"):
        try:
            return value.tobytes().decode("utf-8", errors="ignore")
        except Exception:
            return str(value)
    elif isinstance(value, (np.integer, np.floating, np.bool_)):
        return str(value)
    elif isinstance(value, np.ndarray):
        try:
            # For string arrays, decode bytes
            if value.dtype.kind in ["U", "S"]:  # Unicode or byte string
                return str(value.item())
            else:
                return value.tobytes().decode("utf-8", errors="ignore")
        except Exception:
            return str(value)
    elif not isinstance(value, str):
        try:
            # Try decode as bytes first
            if hasattr(value, "decode"):
                return value.decode("utf-8", errors="ignore")
            else:
                return str(value)
        except Exception:
            return str(value)
    else:
        return value
