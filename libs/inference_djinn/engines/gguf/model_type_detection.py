"""
Model type detection from GGUF metadata.

Detects model family/type from GGUF architecture, name, and tokenizer metadata
using heuristic string matching.
"""

from .gguf_metadata import GGUFMetadataLite
from .metadata_values import _safe_numpy_to_string

# Private module constants lifted from inline dicts
_ARCHITECTURE_LABELS = {
    "llama": "llama",
    "mistral": "mistral",
    "mixtral": "mistral",
    "qwen": "qwen",
    "codellama": "codellama",
}

_MODEL_NAME_PATTERNS = {
    "wizard-vicuna": ["wizard-vicuna", "wizard_vicuna"],
    "vicuna": ["vicuna"],
    "hermes": ["hermes", "nous-hermes"],
    "openchat": ["openchat"],
    "deepseek": ["deepseek"],
    "phind": ["phind"],
    "dolphin": ["dolphin"],
    "orca": ["orca"],
}


def detect_model_type_from_metadata(meta: GGUFMetadataLite) -> str:
    """
    Heuristically determine model type from GGUF metadata.
    1. architecture field (string match)
    2. name and tokenizer_model fields
    3. unknown if no match
    """
    # Safely convert all fields to strings
    arch = _safe_numpy_to_string(
        getattr(meta, "architecture", "unknown"), "unknown"
    ).lower()
    name = _safe_numpy_to_string(getattr(meta, "name", ""), "")
    tokenizer_model = _safe_numpy_to_string(getattr(meta, "tokenizer_model", ""), "")

    name_and_tokenizer = f"{name} {tokenizer_model}".lower()

    # ─── Step 1: Check architecture field ───
    for known_arch, label in _ARCHITECTURE_LABELS.items():
        if known_arch in arch:
            return label

    # ─── Step 2: Check name and tokenizer fields ───
    for model_type, keywords in _MODEL_NAME_PATTERNS.items():
        if any(keyword in name_and_tokenizer for keyword in keywords):
            return model_type

    # ─── Step 3: Fallback ───
    return "unknown"
