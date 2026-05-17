"""
Chat template analysis for GGUF models.

Extracts raw GGUF chat templates and analyzes chat-template support/recommendations,
including special handling for wizard-vicuna models.
"""

import os
from typing import Any

from universal_logging import get_logger

from .gguf_metadata import GGUFMetadataLite
from .metadata_loading import load_gguf_metadata
from .model_type_detection import detect_model_type_from_metadata

logger = get_logger(__name__)

try:
    from gguf_parser import GGUFParser

    gguf_parser_available = True
except ImportError:
    gguf_parser_available = False


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
