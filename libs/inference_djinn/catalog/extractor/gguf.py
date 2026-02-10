"""
GGUF metadata extraction.
"""

from universal_logging import get_logger
from pathlib import Path
from typing import Any

from .base import CatalogMetadata

logger = get_logger(__name__)

# Apply GGUF quantization type patch once at module load
try:
    from ...engines.gguf.gguf_patch import patch_gguf

    _ = patch_gguf()
except Exception:
    pass  # Graceful degradation if gguf not installed


def extract_gguf(path: Path) -> CatalogMetadata:
    """
    Extract metadata from GGUF file using GGUFMetadataLite.

    Args:
        path: Path to GGUF file

    Returns:
        Extracted metadata
    """
    try:
        from gguf import GGUFReader

        from ...engines.gguf.gguf_metadata import GGUFMetadataLite

        reader = GGUFReader(str(path), "r")
        meta = GGUFMetadataLite.from_gguf(reader)
        meta_dict = meta.to_dict()

        # Get architecture directly from GGUF metadata
        arch = meta_dict.get("architecture")
        if arch and arch != "unknown":
            arch = arch.lower()
        else:
            arch = None

        # Family can be inferred from arch if needed, but we don't hardcode mappings
        # Let the catalog consumer decide based on arch
        family = None

        # Get quantization directly from GGUF metadata (avoid filename parsing)
        quant = _normalize_gguf_quant(meta_dict.get("file_type"))

        # Check for chat template
        has_chat_template = bool(meta_dict.get("chat_template"))

        # Determine input schema
        # If chat template exists, use messages
        # For Instruct models without embedded templates, infer from name
        if has_chat_template:
            input_schema = "messages"
            supports_chat_history = True
        elif "instruct" in path.stem.lower() or "chat" in path.stem.lower():
            # Instruct/Chat models support messages even without embedded template
            input_schema = "messages"
            supports_chat_history = True
        else:
            input_schema = "prompt"
            supports_chat_history = False

        # Estimate parameters from GGUF metadata
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
        )
    except Exception as e:
        logger.warning(f"Failed to read GGUF metadata: {e}")
        return CatalogMetadata(
            name=path.stem,
            format="gguf",
        )


def _normalize_gguf_quant(file_type: object | None) -> str | None:
    """Normalize GGUF file_type value into a catalog-friendly quant string."""
    if not file_type or file_type == "unknown":
        return None

    name: str | None = None

    if isinstance(file_type, str):
        name = file_type
    elif hasattr(file_type, "name"):
        name = getattr(file_type, "name")

    if name is None and isinstance(file_type, int):
        try:
            from ...engines.gguf.gguf_patch import get_quantization_type_name

            name = get_quantization_type_name(file_type)
        except Exception as e:
            logger.debug(
                f"Failed to get quantization type name for file_type={file_type}: {e}"
            )
            name = None

    if name is None:
        name = str(file_type)

    return name.replace("GGML_TYPE_", "").replace("GGML_", "")


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
