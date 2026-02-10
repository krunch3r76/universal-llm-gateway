"""
GGUF model inspector and analysis utilities.

Provides functions to analyze GGUF models without loading them:
- Chat template detection
- Model capability analysis
- Configuration recommendations
- Wizard-vicuna specific handling
"""

from universal_logging import get_logger
import os
import pprint
import time
from typing import Any

from .gguf_metadata import GGUFMetadataLite

logger = get_logger(__name__)

# Metadata cache for inspector utility functions: {(model_path, mtime): GGUFMetadataLite}
_metadata_cache: dict[tuple[str, float], GGUFMetadataLite] = {}

try:
    from gguf_parser import GGUFParser

    gguf_parser_available = True
except ImportError:
    gguf_parser_available = False

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


def extract_chat_template_ascii(model_path: str) -> str | None:
    """
    Extract chat template from GGUF file and convert to ASCII.

    Args:
        model_path: Path to the GGUF model file

    Returns:
        ASCII-encoded chat template string, or None if not found/extractable
    """
    if not gguf_parser_available or not os.path.isfile(model_path):
        logger.debug(
            f"Cannot extract chat template from {model_path}: parser unavailable or file not found"
        )
        return None

    try:
        parser = GGUFParser(model_path)
        parser.parse()

        if hasattr(parser, "metadata") and parser.metadata:
            chat_template = parser.metadata.get("tokenizer.chat_template")
            if chat_template and isinstance(chat_template, str):
                # Convert to ASCII, replacing non-ASCII characters with closest equivalents
                ascii_template = chat_template.encode("ascii", errors="replace").decode(
                    "ascii"
                )
                logger.info(
                    f"Extracted chat template from GGUF {model_path}: {ascii_template}"
                )
                return ascii_template
            else:
                logger.debug(
                    f"No chat template found in GGUF metadata for {model_path}"
                )
        else:
            logger.debug(f"No metadata found in GGUF file {model_path}")
    except Exception:
        pass

    return None


def detect_model_type_from_metadata(meta: GGUFMetadataLite) -> str:
    """
    Heuristically determine model type from GGUF metadata.
    1. architecture field (string match)
    2. name and tokenizer_model fields
    3. unknown if no match
    """

    def _safe_numpy_to_string(value, default="unknown"):
        """Safely convert numpy types to strings."""
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

    # Safely convert all fields to strings
    arch = _safe_numpy_to_string(
        getattr(meta, "architecture", "unknown"), "unknown"
    ).lower()
    name = _safe_numpy_to_string(getattr(meta, "name", ""), "")
    tokenizer_model = _safe_numpy_to_string(getattr(meta, "tokenizer_model", ""), "")

    name_and_tokenizer = f"{name} {tokenizer_model}".lower()

    # ─── Step 1: Check architecture field ───
    architecture_map = {
        "llama": "llama",
        "mistral": "mistral",
        "mixtral": "mistral",
        "qwen": "qwen",
        "codellama": "codellama",
    }
    for known_arch, label in architecture_map.items():
        if known_arch in arch:
            return label

    # ─── Step 2: Check name and tokenizer fields ───
    patterns = {
        "wizard-vicuna": ["wizard-vicuna", "wizard_vicuna"],
        "vicuna": ["vicuna"],
        "hermes": ["hermes", "nous-hermes"],
        "openchat": ["openchat"],
        "deepseek": ["deepseek"],
        "phind": ["phind"],
        "dolphin": ["dolphin"],
        "orca": ["orca"],
    }
    for model_type, keywords in patterns.items():
        if any(keyword in name_and_tokenizer for keyword in keywords):
            return model_type

    # ─── Step 3: Fallback ───
    return "unknown"


def detect_chat_template_support(
    model_path: str, meta: GGUFMetadataLite | None = None
) -> dict[str, Any]:
    """
    Analyze GGUF model for chat template support.

    Args:
        model_path: Path to the GGUF model file or directory
        meta: Optional GGUFMetadataLite object (will load if not provided)

    Returns:
        Dictionary with chat template analysis results
    """
    if meta is None:
        meta = load_gguf_metadata(model_path)

    model_name = os.path.basename(model_path).lower()
    analysis = {
        "model_path": model_path,
        "model_name": model_name,
        "has_chat_template": False,
        "template_type": "none",
        "wizard_vicuna_override": False,
        "tokenizer_available": False,
        "recommendations": {},
    }

    if meta is None:
        analysis.update(
            {
                "template_type": "simple_formatting",
                "recommendations": {
                    "use_simple_formatting": True,
                    "reason": "Cannot load GGUF metadata, use simple message concatenation",
                },
            }
        )
        return analysis

    # Check for wizard-vicuna models first
    model_type = detect_model_type_from_metadata(meta)
    if model_type == "wizard-vicuna":
        analysis.update(
            {
                "wizard_vicuna_override": True,
                "template_type": "no_gguf_template",
                "has_chat_template": False,
                "recommendations": {
                    "use_truncation": True,
                    "remove_system_messages": True,
                    "keep_last_user_only": True,
                    "reason": "Wizard-Vicuna models work best with aggressive message truncation. Check model documentation for recommended prompt format.",
                },
            }
        )
        return analysis

    # Check for chat template in metadata
    if meta.chat_template:
        analysis.update(
            {
                "has_chat_template": True,
                "template_type": "gguf_metadata",
                "chat_template": meta.chat_template,
                "tokenizer_available": True,
                "recommendations": {
                    "use_gguf_template": True,
                    "reason": "Model has built-in chat template in GGUF metadata",
                },
            }
        )
        return analysis

    # Fallback to simple formatting
    analysis.update(
        {
            "template_type": "simple_formatting",
            "recommendations": {
                "use_simple_formatting": True,
                "reason": "No chat template detected, use simple message concatenation",
            },
        }
    )

    return analysis


def analyze_model_capabilities(
    model_path: str, meta: GGUFMetadataLite | None = None
) -> dict[str, Any]:
    """
    Analyze GGUF model capabilities and configuration.

    Args:
        model_path: Path to the GGUF model file
        meta: Optional GGUFMetadataLite object (will load if not provided)

    Returns:
        Dictionary with model capability analysis
    """
    if meta is None:
        meta = load_gguf_metadata(model_path)

    capabilities = {
        "model_path": model_path,
        "file_exists": os.path.exists(model_path),
        "file_size_mb": 0,
        "estimated_parameters": "unknown",
        "format": "gguf",
        "chat_template_analysis": None,
        "recommended_config": {},
    }

    if capabilities["file_exists"]:
        try:
            file_size = os.path.getsize(model_path)
            capabilities["file_size_mb"] = round(file_size / (1024 * 1024), 2)
        except Exception as e:
            capabilities["file_error"] = str(e)

    # Add metadata information if available
    if meta:
        capabilities.update(
            {
                "metadata": {
                    "name": meta.name,
                    "architecture": meta.architecture,
                    "context_length": meta.context_length,
                    "embedding_length": meta.embedding_length,
                    "block_count": meta.block_count,
                    "head_count": meta.head_count,
                    "tokenizer_model": meta.tokenizer_model,
                    "chat_template_available": bool(meta.chat_template),
                }
            }
        )

        # Estimate parameters from metadata
        if meta.block_count > 0 and meta.embedding_length > 0:
            # Rough parameter estimation
            params_estimate = (
                meta.block_count * meta.embedding_length * 4
            )  # Very rough estimate
            if params_estimate > 1e9:
                capabilities["estimated_parameters"] = f"~{params_estimate / 1e9:.1f}B"
            elif params_estimate > 1e6:
                capabilities["estimated_parameters"] = f"~{params_estimate / 1e6:.0f}M"

    # Analyze chat template support
    capabilities["chat_template_analysis"] = detect_chat_template_support(
        model_path, meta
    )

    # Generate recommended configuration
    capabilities["recommended_config"] = (
        generate_recommended_config(meta) if meta else {}
    )

    return capabilities


def generate_recommended_config(meta: GGUFMetadataLite) -> dict[str, Any]:
    """
    Generate recommended llama-cpp-python config based on GGUF metadata.

    Args:
        meta: GGUFMetadataLite with parsed model metadata.

    Returns:
        Recommended llama-cpp config.
    """
    cpu_count = os.cpu_count() or 8

    # ─── Base Config ───
    config = {
        "n_ctx": meta.context_length,
        "n_gpu_layers": -1,  # Default to full offload (adjust based on GPU VRAM externally if needed)
        "n_batch": 512,
        "n_threads": min(cpu_count, 16),
        "verbose": False,
        "use_mlock": True,
        "use_mmap": True,
        "f16_kv": True,
    }

    # ─── Adjust n_batch Based on Model Size ───
    # Use float arithmetic to avoid overflow
    param_estimate = (
        float(meta.block_count)
        * float(meta.embedding_length)
        * float(meta.feed_forward_length)
        * float(meta.head_count)
    )
    if (
        param_estimate > 50_000_000_000
    ):  # Arbitrary high threshold (50B "parameter proxy")
        config.update(
            {
                "n_batch": 256,
                "n_threads": min(cpu_count, 16),
            }
        )
    elif param_estimate < 7_000_000_000:  # Small models, likely 7B-class or smaller
        config.update(
            {
                "n_batch": 1024,
                "n_ctx": min(
                    8192, meta.context_length * 2
                ),  # Cap ctx to double the trained context length
            }
        )

    # ─── Adjust for Chat Template ───
    def _safe_numpy_to_string_local(value, default=""):
        """Safely convert numpy types to strings."""
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

    chat_template = _safe_numpy_to_string_local(getattr(meta, "chat_template", ""), "")

    if chat_template and "vicuna" in chat_template.lower():
        config["force_chat_template"] = True

    # ─── Adjust ROPE Scaling (if applicable) ───
    if config["n_ctx"] > meta.context_length:
        config["rope_scaling_type"] = "linear"
        config["rope_freq_base"] = meta.rope_freq_base

    # ─── Platform-Specific mlock Handling ───
    if os.name == "nt":  # Windows
        config["use_mlock"] = False

    return config


def validate_model_requirements(
    model_path: str, meta: GGUFMetadataLite | None = None
) -> dict[str, Any]:
    """
    Validate that model meets requirements for GGUF engine.

    Args:
        model_path: Path to the model file
        meta: Optional GGUFMetadataLite object (will load if not provided)

    Returns:
        Validation results dictionary
    """
    if meta is None:
        meta = load_gguf_metadata(model_path)

    validation = {"valid": True, "errors": [], "warnings": [], "requirements_met": True}

    # Check file exists
    if not os.path.exists(model_path):
        validation["errors"].append(f"Model file not found: {model_path}")
        validation["valid"] = False
        return validation

    # Check file extension
    if not model_path.lower().endswith((".gguf", ".ggml")):
        validation["warnings"].append(
            f"File extension suggests non-GGUF format: {model_path}"
        )

    # Check metadata loading
    if meta is None:
        validation["warnings"].append(
            "Could not load GGUF metadata - may indicate format issues"
        )
    else:
        # Validate metadata content
        if meta.architecture == "unknown":
            validation["warnings"].append("Model architecture not recognized")
        if meta.context_length <= 0:
            validation["warnings"].append("Context length not specified or invalid")
        if meta.block_count <= 0:
            validation["warnings"].append("Block count not specified or invalid")

    # Check file size (minimum reasonable size)
    try:
        file_size = os.path.getsize(model_path)
        if file_size < 10 * 1024 * 1024:  # < 10MB
            validation["warnings"].append(
                "File size is very small - may not be a complete model"
            )
        elif file_size > 100 * 1024 * 1024 * 1024:  # > 100GB
            validation["warnings"].append(
                "File size is very large - may require special handling"
            )
    except Exception as e:
        validation["errors"].append(f"Could not check file size: {e}")

    # Check dependencies
    try:
        import llama_cpp
    except ImportError:
        validation["errors"].append(
            "llama-cpp-python not available - required for GGUF engine"
        )
        validation["valid"] = False

    if validation["errors"]:
        validation["valid"] = False
        validation["requirements_met"] = False

    return validation


def get_model_info_summary(model_path: str) -> dict[str, Any]:
    """
    Get complete model information summary for GGUF model.

    Args:
        model_path: Path to the model file

    Returns:
        Complete model information dictionary
    """
    logger.debug("\033[94mLoading GGUF metadata\033[0m")
    meta = load_gguf_metadata(model_path)

    logger.debug("\033[94mPreparing caller info string\033[0m")
    caller_info = f"{__file__}:{get_model_info_summary.__name__}"

    logger.debug("\033[94mGGUF Inspector function call location logged\033[0m")

    logger.debug("\033[94mDetecting model type from metadata\033[0m")
    model_type = detect_model_type_from_metadata(meta) if meta else "unknown"

    logger.debug("\033[94mAnalyzing model capabilities\033[0m")
    capabilities = analyze_model_capabilities(model_path, meta)

    logger.debug("\033[94mDetecting chat template support\033[0m")
    chat_template = detect_chat_template_support(model_path, meta)

    logger.debug("\033[94mValidating model requirements\033[0m")
    validation = validate_model_requirements(model_path, meta)

    logger.debug("\033[94mAssembling model info dictionary\033[0m")
    info = {
        "model_path": model_path,
        "model_type": model_type,
        "capabilities": capabilities,
        "chat_template": chat_template,
        "validation": validation,
        "metadata": meta.to_dict() if meta else None,
        "engine": "gguf",
    }

    # Log the complete info summary
    logger.info(
        f"📊 Complete Model Info Summary ({caller_info}):\n{pprint.pformat(info)}"
    )

    if meta:
        logger.info(
            f"📝 GGUF Metadata (dict, {caller_info}):\n{pprint.pformat(meta.to_dict())}"
        )
    else:
        logger.info(f"📝 GGUF Metadata (dict, {caller_info}): None")

    return info
