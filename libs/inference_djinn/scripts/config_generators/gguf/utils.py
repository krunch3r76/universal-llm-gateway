"""
Utility Functions

Helper functions for metadata extraction, sanitization, and common operations.
"""

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# Apply GGUF patch to support newer quantization types before importing GGUFReader
try:
    from inference_djinn.engines.gguf.gguf_patch import patch_gguf

    patch_gguf()
except ImportError as e:
    # llama_cpp dependency checked at entry point - if we get here, gguf library may be missing
    if "llama_cpp" in str(e):
        print(f"Warning: Could not apply GGUF patch: {e}", file=sys.stderr)
        print("Note: llama_cpp availability is checked at startup", file=sys.stderr)
    else:
        print(
            f"Warning: Could not apply GGUF patch (gguf library issue): {e}",
            file=sys.stderr,
        )
except Exception as e:
    print(f"Warning: Could not apply GGUF patch: {e}", file=sys.stderr)

try:
    from gguf import GGUFReader
except ImportError:
    GGUFReader = None

try:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gguf_metadata",
        Path(__file__).parent.parent.parent.parent
        / "engines"
        / "gguf"
        / "gguf_metadata.py",
    )
    gguf_metadata_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gguf_metadata_module)
    GGUFMetadataLite = gguf_metadata_module.GGUFMetadataLite
except Exception as e:
    print(f"Warning: Could not load GGUFMetadataLite: {e}", file=sys.stderr)
    GGUFMetadataLite = None


def to_native_int(value: Any) -> int | None:
    """
    Convert numpy/scalar types to Python native int.

    Handles numpy.uint32, numpy.int64, and other numeric types.
    Returns None if value is None, otherwise converts to int.
    """
    if value is None:
        return None

    # Handle numpy types
    try:
        import numpy as np

        if isinstance(value, (np.integer, np.floating)):
            return int(value)
    except ImportError:
        pass

    # Handle other numeric types
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def normalize_family(architecture: str) -> str:
    """Normalize architecture to family name."""
    arch_lower = architecture.lower()

    families = {
        "llama": "llama",
        "mistral": "mistral",
        "mixtral": "mistral",
        "qwen": "qwen",
        "phi": "phi",
        "gemma": "gemma",
        "gpt-oss": "gpt-oss",
    }

    for pattern, family in families.items():
        if pattern in arch_lower:
            return family

    return arch_lower


def extract_quant_from_filename(filename: str) -> str | None:
    """Extract quantization type from filename (e.g., Q4_K_M, Q5_K_S)."""
    quant_pattern = r"\b(Q[2-8]_[KM](?:_[SMLX])?|F16|F32|IQ[0-9]_[SMLX]{1,2})\b"
    match = re.search(quant_pattern, filename, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


def extract_params_from_filename(filename: str) -> int | None:
    """
    Extract parameter count from filename if clearly indicated.
    Returns integer (e.g., 7B -> 7000000000) or None.
    """
    param_pattern = r"\b(\d+(?:\.\d+)?)[Bb]\b"
    match = re.search(param_pattern, filename)
    if match:
        try:
            param_value = float(match.group(1))
            return int(param_value * 1_000_000_000)
        except ValueError:
            pass
    return None


def sanitize_model_id(filename: str) -> str:
    """Sanitize filename to create internal_model_id."""
    name = filename.replace(".gguf", "")
    name = re.sub(r"[^a-z0-9]+", "-", name.lower())
    name = name.strip("-")
    return name


def get_optional_field(reader: Any, key: str) -> Any | None:
    """Safely extract optional field from GGUF, return None if missing."""
    if GGUFReader is None or reader is None:
        return None

    try:
        field = reader.get_field(key)
        if field and hasattr(field, "parts") and len(field.parts) > 0:
            value = field.parts[-1]
            if hasattr(value, "tobytes"):
                decoded = (
                    value.tobytes()
                    .decode("utf-8", errors="ignore")
                    .rstrip("\x00")
                    .strip()
                )
                return decoded if decoded else None
            elif hasattr(value, "decode"):
                return value.decode("utf-8", errors="ignore").rstrip("\x00").strip()
            return str(value).strip() if value else None
    except Exception:
        pass
    return None


def extract_metadata(model_path: str) -> tuple[Any | None, Any | None]:
    """
    Extract metadata from GGUF file.

    Returns:
        Tuple of (GGUFMetadataLite, GGUFReader) or (None, None) if extraction fails
    """
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model file not found: {model_path}")

    if not model_path.endswith(".gguf"):
        raise ValueError(f"File does not have .gguf extension: {model_path}")

    if GGUFReader is None or GGUFMetadataLite is None:
        print(
            "Warning: GGUF reader not available, skipping metadata extraction",
            file=sys.stderr,
        )
        return None, None

    try:
        reader = GGUFReader(model_path)
        meta = GGUFMetadataLite.from_gguf(reader)
        return meta, reader
    except Exception as e:
        print(f"Warning: Failed to extract GGUF metadata: {e}", file=sys.stderr)
        return None, None


def compute_cache_key(model_path: str) -> str:
    """Compute cache key for model configuration."""
    try:
        with open(model_path, "rb") as f:
            f.seek(0)
            start = f.read(4096)
            f.seek(-4096, 2)
            end = f.read(4096)
            model_hash = hashlib.sha256(start + end).hexdigest()[:16]
    except Exception:
        model_hash = hashlib.sha256(os.path.abspath(model_path).encode()).hexdigest()[
            :16
        ]

    return model_hash


def get_cache_path(cache_key: str) -> Path:
    """Get path to cache file."""
    cache_dir = Path.home() / ".cache" / "inference_djinn" / "gguf_capacity"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{cache_key}.json"


def clear_legacy_cache() -> None:
    """Remove all existing cache files - no backward compatibility."""
    try:
        cache_dir = Path.home() / ".cache" / "inference_djinn" / "gguf_capacity"
        if cache_dir.exists():
            import shutil

            shutil.rmtree(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            print("Cleared legacy cache", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Failed to clear legacy cache: {e}", file=sys.stderr)


def check_gpu_available() -> tuple[bool, str | None]:
    """Check if GPU is available via nvidia-smi."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return True, result.stdout.strip()
        return False, None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, None


def check_gpu_idle(
    gpu_index: int = 0, threshold_mb: int = 600
) -> tuple[bool, int, int]:
    """
    Check if GPU is mostly idle (used VRAM <= threshold).

    Returns:
        (is_idle, used_mb, total_mb)
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                f"--id={gpu_index}",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            used_str, total_str = result.stdout.strip().split(",")
            used_mb = int(used_str.strip())
            total_mb = int(total_str.strip())
            return used_mb <= threshold_mb, used_mb, total_mb
        return False, 0, 0
    except Exception:
        return False, 0, 0


def find_model_file(model_path: str) -> str | None:
    """
    Find the actual model file by searching common locations.

    Returns:
        Absolute path to the model file if found, None otherwise
    """
    if os.path.exists(model_path):
        return os.path.abspath(model_path)

    abs_path = os.path.abspath(model_path)
    if os.path.exists(abs_path):
        return abs_path

    filename = os.path.basename(model_path)
    if filename != model_path:
        return None

    search_paths = [
        "/mnt/torus/models",
        "/models",
        os.path.expanduser("~/models"),
        os.path.expanduser("~/.models"),
    ]

    for search_dir in search_paths:
        test_path = os.path.join(search_dir, filename)
        if os.path.exists(test_path):
            return os.path.abspath(test_path)

    return None


def determine_contexts(
    training_context_length: int | None, custom_contexts_str: str | None
) -> list[int]:
    """
    Determine which context lengths to test.

    If custom contexts specified, use those. Otherwise use training context length.
    Raises ValueError if neither is available.
    """
    if custom_contexts_str:
        try:
            contexts = [int(x.strip()) for x in custom_contexts_str.split(",")]
            return sorted(contexts, reverse=True)
        except ValueError:
            raise ValueError(f"Invalid contexts format: {custom_contexts_str}")

    # Convert numpy types to native Python int
    training_context_length = to_native_int(training_context_length)

    if training_context_length and training_context_length > 0:
        # Ensure we return a list of native Python ints
        return [int(training_context_length)]

    # No fallback - require explicit context specification
    raise ValueError(
        "Cannot determine context length: "
        "GGUF metadata extraction failed (GGUF reader not available). "
        "Please specify --contexts explicitly, e.g., --contexts 4096"
    )
