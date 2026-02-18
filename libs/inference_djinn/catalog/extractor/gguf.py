"""
GGUF metadata extraction.

Quant derivation: filename is the authoritative source for quantization type.
general.file_type in GGUF metadata is a *file-level* enum (GGUFFileType)
whose integer values do NOT correspond to tensor-level GGMLQuantizationType.
"""

import re
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from .base import CatalogMetadata

logger = get_logger(__name__)

# Apply GGUF quantization type patch once at module load
try:
    from ...engines.gguf.gguf_patch import patch_gguf

    _ = patch_gguf()
except Exception:
    pass

# Ordered longest-first to prevent partial matches (e.g. Q4_K before Q4_K_M)
_QUANT_TOKENS: list[str] = [
    "Q4_K_XL", "Q6_K_XL", "Q8_K_XL",
    "Q3_K_S", "Q3_K_M", "Q3_K_L",
    "Q4_K_S", "Q4_K_M", "Q4_K_L",
    "Q5_K_S", "Q5_K_M", "Q5_K_L",
    "Q6_K_L", "Q6_K_M",
    "Q2_K", "Q3_K", "Q4_K", "Q5_K", "Q6_K", "Q8_K",
    "Q4_0_4_4", "Q4_0_4_8", "Q4_0_8_8",
    "Q4_0", "Q4_1", "Q5_0", "Q5_1", "Q8_0", "Q8_1",
    "IQ2_XXS", "IQ2_XS", "IQ2_S", "IQ2_M",
    "IQ3_XXS", "IQ3_S", "IQ3_M",
    "IQ4_NL", "IQ4_XS",
    "IQ1_S", "IQ1_M",
    "TQ1_0", "TQ2_0",
    "BF16", "F16", "F32",
    "MXFP4",
]  # fmt: skip

_QUANT_RE_BODY = "|".join(re.escape(t) for t in _QUANT_TOKENS)
_QUANT_PATTERN = re.compile(
    rf"(?:^|[.\-_])({_QUANT_RE_BODY})(?:[.\-_]|$)",
    re.IGNORECASE,
)

# general.file_type → quant string (GGUFFileType enum, NOT GGMLQuantizationType)
_GGUF_FILE_TYPE_TO_QUANT: dict[int, str] = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1",
    7: "Q8_0", 8: "Q5_0", 9: "Q5_1",
    10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L",
    14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
    19: "IQ2_XXS", 20: "IQ2_XS", 21: "IQ3_XXS", 22: "IQ1_S",
    23: "IQ4_NL", 24: "IQ3_S", 25: "IQ3_M", 26: "IQ2_S", 27: "IQ2_M",
    28: "IQ4_XS", 29: "IQ1_M", 30: "BF16",
    31: "Q4_0_4_4", 32: "Q4_0_4_8", 33: "Q4_0_8_8",
    34: "TQ1_0", 35: "TQ2_0",
}  # fmt: skip


def extract_quant_from_filename(filename: str) -> str | None:
    """Extract quantization type from a filename by matching known quant tokens.

    Handles both underscore and dash separators.
    e.g. "model.Q4_K_M.gguf" → "Q4_K_M", "model-q8-0.gguf" → "Q8_0"

    Args:
        filename: Filename or stem (with or without .gguf extension).

    Returns:
        Canonical uppercase quant string, or None if not found.
    """
    stem = filename.rsplit(".", 1)[0] if filename.endswith(".gguf") else filename
    normalized = stem.upper().replace("-", "_")

    match = _QUANT_PATTERN.search(normalized)
    if match:
        raw = match.group(1).upper()
        for token in _QUANT_TOKENS:
            if raw == token:
                return token
    return None


def extract_gguf(path: Path) -> CatalogMetadata:
    """Extract metadata from GGUF file using GGUFMetadataLite.

    Args:
        path: Path to GGUF file.

    Returns:
        Extracted metadata.
    """
    try:
        from gguf import GGUFReader

        from ...engines.gguf.gguf_metadata import GGUFMetadataLite

        reader = GGUFReader(str(path), "r")
        meta = GGUFMetadataLite.from_gguf(reader)
        meta_dict = meta.to_dict()

        arch = meta_dict.get("architecture")
        if arch and arch != "unknown":
            arch = arch.lower()
        else:
            arch = None

        family = None

        quant = extract_quant_from_filename(path.name)
        if quant is None:
            quant = _resolve_file_type(meta_dict.get("file_type"))

        has_chat_template = bool(meta_dict.get("chat_template"))

        if has_chat_template:
            input_schema = "messages"
            supports_chat_history = True
        elif "instruct" in path.stem.lower() or "chat" in path.stem.lower():
            input_schema = "messages"
            supports_chat_history = True
        else:
            input_schema = "prompt"
            supports_chat_history = False

        parameters_m = _estimate_parameters(meta_dict)

        return CatalogMetadata(
            name=path.stem,
            format="gguf",
            family=family,
            arch=arch,
            quant=quant,
            parameters_m=parameters_m,
            training_context_length=meta_dict.get("context_length"),
            supports_chat_history=supports_chat_history,
            input_schema=input_schema,
            has_chat_template=has_chat_template,
            extra=meta_dict,
        )

    except ImportError:
        logger.warning("gguf package not installed, using minimal extraction")
        return CatalogMetadata(
            name=path.stem,
            format="gguf",
            quant=extract_quant_from_filename(path.name),
        )
    except Exception as e:
        logger.warning(f"Failed to read GGUF metadata: {e}")
        return CatalogMetadata(
            name=path.stem,
            format="gguf",
            quant=extract_quant_from_filename(path.name),
        )


def _resolve_file_type(file_type: object | None) -> str | None:
    """Map general.file_type (GGUFFileType enum) to a quant string.

    This is the fallback when filename-based extraction fails. Uses the correct
    GGUFFileType → quant mapping (NOT GGMLQuantizationType which is tensor-level).

    Args:
        file_type: Value from general.file_type GGUF metadata field.

    Returns:
        Canonical quant string or None.
    """
    if file_type is None or file_type == "unknown":
        return None

    if isinstance(file_type, int):
        resolved = _GGUF_FILE_TYPE_TO_QUANT.get(file_type)
        if resolved is None:
            logger.warning(f"Unknown GGUF file_type integer: {file_type}")
        return resolved

    if isinstance(file_type, str):
        cleaned = file_type.replace("MOSTLY_", "").replace("ALL_", "")
        return cleaned if cleaned else None

    if hasattr(file_type, "name"):
        name = str(getattr(file_type, "name"))
        return name.replace("MOSTLY_", "").replace("ALL_", "")

    logger.warning(f"Unrecognized file_type type: {type(file_type).__name__}")
    return None


def _estimate_parameters(meta: dict[str, Any]) -> int | None:
    """Estimate parameter count from GGUF metadata."""
    block_count = meta.get("block_count", 0)
    embedding_length = meta.get("embedding_length", 0)
    feed_forward_length = meta.get("feed_forward_length", 0)

    if not all([block_count, embedding_length]):
        return None

    # Rough estimation: 12 * n_layers * d_model^2
    # This is approximate and varies by architecture
    params = 12 * block_count * embedding_length * embedding_length

    if feed_forward_length:
        # Add FFN parameters: 2 * n_layers * d_model * d_ff
        params += 2 * block_count * embedding_length * feed_forward_length

    return params // 1_000_000  # Convert to millions
