"""Vision model detection for GGUF config generator."""

from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

# Patterns in model names/paths that indicate vision models
VISION_NAME_PATTERNS: dict[str, str] = {
    "qwen2-vl": "qwen2_vl",
    "qwen2.5-vl": "qwen2_vl",
    "qwen-vl": "qwen2_vl",
    "llava-1.5": "llava_1_5",
    "llava-v1.5": "llava_1_5",
    "llava-1.6": "llava_1_6",
    "llava-v1.6": "llava_1_6",
    "llava-next": "llava_1_6",
    "minicpm-v": "minicpm_v",
    "minicpmv": "minicpm_v",
    "moondream": "moondream",
    "ministral": "mistral3",
    "mistral-3": "mistral3",
    "mistral3": "mistral3",
}

# Common mmproj file patterns
MMPROJ_PATTERNS: list[str] = [
    "mmproj*.gguf",
    "*mmproj*.gguf",
    "*-mmproj-*.gguf",
    "*_mmproj_*.gguf",
    "clip*.gguf",
    "*clip*.gguf",
    "*vision*.gguf",
]


def detect_vision_architecture(model_path: str) -> str | None:
    """
    Detect vision architecture from model path/name.

    Args:
        model_path: Path to the GGUF model file

    Returns:
        Vision architecture key or None if not a vision model
    """
    model_name = Path(model_path).stem.lower()

    for pattern, architecture in VISION_NAME_PATTERNS.items():
        if pattern in model_name:
            logger.info(
                f"🔮 Detected vision model: {architecture} (matched '{pattern}')"
            )
            return architecture

    return None


def find_mmproj_file(model_path: str) -> str | None:
    """
    Find the mmproj/CLIP model file for a vision model.

    Searches in:
    1. Same directory as model
    2. Parent directory
    3. Subdirectory named 'clip' or 'vision'

    Args:
        model_path: Path to the main GGUF model file

    Returns:
        Path to mmproj file or None if not found
    """
    model_dir = Path(model_path).parent
    search_dirs = [
        model_dir,
        model_dir.parent,
        model_dir / "clip",
        model_dir / "vision",
    ]

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue

        for pattern in MMPROJ_PATTERNS:
            matches = list(search_dir.glob(pattern))
            if matches:
                # Prefer exact mmproj match
                for match in matches:
                    if "mmproj" in match.name.lower():
                        logger.info(f"🔮 Found mmproj file: {match}")
                        return str(match)
                # Fall back to first match
                logger.info(f"🔮 Found CLIP file: {matches[0]}")
                return str(matches[0])

    logger.warning(
        f"⚠️ No mmproj/CLIP file found for vision model. "
        f"Searched: {[str(d) for d in search_dirs if d.exists()]}"
    )
    return None


def get_vision_config_fields(model_path: str) -> dict[str, str | None] | None:
    """
    Get vision configuration fields for a model.

    Returns dict with vision_architecture and clip_model_path if vision model,
    None otherwise.
    """
    architecture = detect_vision_architecture(model_path)
    if not architecture:
        return None

    mmproj_path = find_mmproj_file(model_path)

    return {
        "vision_architecture": architecture,
        "clip_model_path": mmproj_path,  # May be None if not found
    }
