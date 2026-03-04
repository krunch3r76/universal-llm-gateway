"""
Main Entry Point and CLI

Orchestrates the configuration generation workflow with simplified arguments
and clean control flow.
"""

import argparse
import sys

from .profiles import BaseProfile
from .utils import (
    extract_metadata,
    extract_params_from_filename,
    extract_quant_from_filename,
    normalize_family,
    sanitize_model_id,
)
from .vision_detector import get_vision_config_fields


def build_base_profile_from_metadata(
    model_path: str, owned_by: str = "universal-llm-gateway"
) -> BaseProfile:
    """
    Extract GGUF metadata and build BaseProfile.

    Args:
        model_path: Path to GGUF model file
        owned_by: Owner for OpenAI API fields

    Returns:
        BaseProfile with extracted metadata
    """
    import os

    filename = os.path.basename(model_path)
    abs_path = os.path.abspath(model_path)

    # Extract metadata
    meta, reader = extract_metadata(model_path)
    try:
        base_name = filename.replace(".gguf", "")
        model_id = sanitize_model_id(filename)

        # Determine defaults from filename/metadata
        arch = meta.architecture if meta else "unknown"
        family = normalize_family(arch)
        quant = extract_quant_from_filename(filename)
        parameters_raw = extract_params_from_filename(filename)
        training_context_length_raw = meta.context_length if meta else None
        # Convert numpy types to native Python int
        from .utils import to_native_int

        parameters = to_native_int(parameters_raw) if parameters_raw is not None else None
        training_context_length = to_native_int(training_context_length_raw)
        has_chat_template = bool(meta and meta.chat_template and meta.chat_template.strip())
        input_schema = "messages" if has_chat_template else "prompt"

        # Check for vision model
        vision_config = get_vision_config_fields(model_path)
        vision_architecture = None
        clip_model_path = None
        if vision_config:
            vision_architecture = vision_config.get("vision_architecture")
            clip_model_path = vision_config.get("clip_model_path")
            print(f"🔮 Vision model detected: {vision_architecture}", file=sys.stderr)
            if not clip_model_path:
                print(
                    "⚠️ Vision model detected but mmproj file not found. "
                    "Set clip_model_path manually in config.",
                    file=sys.stderr,
                )

        # Build v4 capabilities
        modalities: dict = (
            {"input": ["text", "vision"], "output": ["text"]}
            if vision_architecture
            else {"input": ["text"], "output": ["text"]}
        )
        if vision_architecture:
            modalities["vision_architecture"] = vision_architecture
        capabilities = {
            "input_schema": input_schema,
            "modalities": modalities,
            "interaction": {"chat_template": has_chat_template},
            "reasoning": {"supports_thinking": False},
            "limits": {"max_context_length": training_context_length}
            if training_context_length
            else {},
            "provenance": {"license": meta.license} if meta and meta.license else {},
        }

        base_profile = BaseProfile(
            name=base_name,
            family=family,
            arch=arch,
            path=abs_path,
            quant=quant,
            parameters=parameters,
            training_context_length=training_context_length,
            license=meta.license if meta else None,
            vision_architecture=vision_architecture,
            clip_model_path=clip_model_path,
            capabilities=capabilities,
            openai_api_fields={
                "id": model_id,
                "object": "model",
                "owned_by": owned_by,
                "permission": ["generate"],
            },
        )
    finally:
        if reader is not None:
            reader.close()

    return base_profile


def create_parser() -> argparse.ArgumentParser:
    """Create simplified argument parser."""
    parser = argparse.ArgumentParser(
        description="Generate GGUF model configuration for universal-llm-gateway",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/model.gguf
  %(prog)s /path/to/model.gguf --cpu-only --contexts 4096,8192
  %(prog)s /path/to/model.gguf --gpu-only
  %(prog)s /path/to/model.gguf --use-cached --push --api-url http://localhost:9998
        """,
    )

    parser.add_argument("model_path", help="Path to GGUF model file")
    parser.add_argument(
        "--contexts",
        help="Comma-separated context lengths (default: training_context_length)",
    )
    parser.add_argument(
        "--cpu-only",
        action="store_true",
        default=False,
        help="Generate CPU-only profile",
    )
    parser.add_argument(
        "--gpu-only",
        action="store_true",
        default=False,
        help="Generate GPU-only profile",
    )
    parser.add_argument(
        "--safe-margin",
        type=int,
        default=1,
        help="Safety margin layers to subtract from max (default: 1)",
    )
    parser.add_argument(
        "--n_gpu_layers",
        type=int,
        default=-1,
        help="Target n_gpu_layers (-1 for all layers, positive for specific count)",
    )
    parser.add_argument(
        "--use-cached",
        action="store_true",
        default=False,
        help="Use cached configuration instead of generating new one",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        default=False,
        help="Push configuration to gateway API",
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:9998",
        help="Gateway API URL (default: http://localhost:9998)",
    )
    parser.add_argument("--api-token", help="API authentication token")
    parser.add_argument(
        "--model-key", help="Model key for API (defaults to ID from config)"
    )
    parser.add_argument("--output", "-o", help="Output file path (default: stdout)")
    parser.add_argument(
        "--owned-by",
        default="universal-llm-gateway",
        help="Owner for OpenAI API fields",
    )
    parser.add_argument(
        "--gpu-index", type=int, default=0, help="GPU index for testing (default: 0)"
    )

    return parser


def main() -> None:
    """Main entry point — deprecated, measurement moved to TUI."""
    print(
        "Standalone CLI measurement removed. Use './manage' TUI → Measure instead.",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
