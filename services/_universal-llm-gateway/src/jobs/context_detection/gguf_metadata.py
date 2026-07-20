"""GGUF metadata extraction for training context length discovery during measurement.

Reads architecture-specific and fallback context fields from on-disk GGUF files
when catalog entries lack authoritative training_context_length metadata.
"""

from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)


def extract_training_context_from_gguf(file_path: Path) -> int | None:
    """
    Extract training_context_length directly from GGUF file metadata.

    Args:
        file_path: Path to GGUF model file

    Returns:
        Training context length or None if extraction fails
    """
    try:
        import gguf

        reader = gguf.GGUFReader(str(file_path))
        fields = reader.fields

        arch_field = fields.get("general.architecture")
        if arch_field and arch_field.data:
            arch_value = arch_field.parts[arch_field.data[0]]
            if hasattr(arch_value, "tobytes"):
                arch = arch_value.tobytes().decode("utf-8").rstrip("\x00").strip()
            elif isinstance(arch_value, str):
                arch = arch_value
            else:
                arch = str(arch_value)

            arch_context_field = f"{arch}.context_length"
            if arch_context_field in fields:
                field = fields[arch_context_field]
                if field.data:
                    value = field.parts[field.data[0]]
                    if isinstance(value, list | tuple) and len(value) > 0:
                        return int(value[0])
                    if hasattr(value, "item"):
                        return int(value.item())
                    return int(value)

        context_field_names = [
            "llama.context_length",
            "context_length",
            "n_ctx_train",
            "max_position_embeddings",
        ]

        for field_name in context_field_names:
            if field_name in fields:
                field = fields[field_name]
                if field.data:
                    value = field.parts[field.data[0]]
                    if isinstance(value, list | tuple) and len(value) > 0:
                        return int(value[0])
                    if hasattr(value, "item"):
                        return int(value.item())
                    return int(value)

        logger.warning(
            f"No context length field found in GGUF metadata for {file_path}"
        )
        return None

    except ImportError:
        logger.error(
            "gguf library not available - cannot extract metadata from GGUF files"
        )
        return None
    except Exception as e:
        logger.warning(
            "Failed to extract training context from GGUF %s: %s",
            file_path,
            e,
            exc_info=True,
        )
        return None
