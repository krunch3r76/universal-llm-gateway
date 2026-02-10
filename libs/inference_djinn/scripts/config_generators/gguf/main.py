"""
Main Entry Point and CLI

Orchestrates the configuration generation workflow with simplified arguments
and clean control flow.
"""

import argparse
import sys

import yaml

from .api_client import push_profile_to_api
from .caching import cache_whole_profile, load_cached_profile
from .profiles import (
    BaseProfile,
    merge_profiles,
)
from .testing import (
    build_subprofile_cpu_only,
    build_subprofile_gpu_only,
    build_subprofile_hybrid,
)
from .utils import (
    clear_legacy_cache,
    determine_contexts,
    extract_metadata,
    extract_params_from_filename,
    extract_quant_from_filename,
    find_model_file,
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

    # Build base info
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

    base_profile = BaseProfile(
        name=base_name,
        family=family,
        arch=arch,
        path=abs_path,
        quant=quant,
        parameters=parameters,
        training_context_length=training_context_length,
        input_schema=input_schema,
        vision_architecture=vision_architecture,
        clip_model_path=clip_model_path,
        openai_api_fields={
            "id": model_id,
            "object": "model",
            "owned_by": owned_by,
            "permission": ["generate"],
        },
    )

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


def main():
    """Main entry point with control flow orchestration."""
    parser = create_parser()
    args = parser.parse_args()

    try:
        # Validate argument combinations
        if args.cpu_only and args.gpu_only:
            raise ValueError("Cannot use both --cpu-only and --gpu-only")

        # Find model file
        model_path = find_model_file(args.model_path)
        if model_path is None:
            raise FileNotFoundError(f"Model file not found: {args.model_path}")

        print(f"Model: {model_path}", file=sys.stderr)

        # Handle --use-cached path
        if args.use_cached:
            print("Loading cached configuration...", file=sys.stderr)
            whole_profile = load_cached_profile(model_path)
            if whole_profile is None:
                print("Error: No cached configuration found", file=sys.stderr)
                sys.exit(1)
        else:
            # Clear legacy cache on first fresh generation
            clear_legacy_cache()

            # Build BaseProfile from metadata
            print("Extracting GGUF metadata...", file=sys.stderr)
            base_profile = build_base_profile_from_metadata(model_path, args.owned_by)

            # Determine contexts
            contexts = determine_contexts(
                base_profile.training_context_length, args.contexts
            )
            print(f"Testing contexts: {contexts}", file=sys.stderr)

            # Build SubProfile based on mode
            cache_config = True
            try:
                if args.cpu_only:
                    sub_profile = build_subprofile_cpu_only(model_path, contexts)
                elif args.gpu_only:
                    sub_profile = build_subprofile_gpu_only(
                        model_path, contexts, args.gpu_index
                    )
                else:
                    # Hybrid mode (default)
                    sub_profile = build_subprofile_hybrid(
                        model_path,
                        contexts,
                        args.n_gpu_layers,
                        args.safe_margin,
                        args.gpu_index,
                    )
            except RuntimeError as e:
                # Context too large for GPU - don't cache
                print(f"⚠️  {e}", file=sys.stderr)
                cache_config = False
                raise

            # Merge to create WholeProfile
            base_loader = {
                "n_batch": 512,
                "f16_kv": True,
                "use_mmap": False,
                "use_mlock": True,
                "verbose": False,
            }
            whole_profile = merge_profiles(base_profile, sub_profile, base_loader)

            # Cache the profile only if generation succeeded
            if cache_config:
                cache_whole_profile(model_path, whole_profile)

        # Validate that the profile has usable measurements before proceeding
        if not whole_profile.has_valid_measurements():
            failed_contexts = whole_profile.get_failed_contexts()
            if failed_contexts:
                raise RuntimeError(
                    f"All GPU layer testing failed for contexts: {failed_contexts}. "
                    f"No valid configuration can be generated. "
                    f"Try smaller contexts with --contexts or use --cpu-only mode."
                )
            else:
                raise RuntimeError(
                    "Configuration generation failed: no valid measurements found. "
                    "Check GPU availability and context sizes."
                )

        # Handle --push
        if args.push:
            model_key = args.model_key or whole_profile.info.get(
                "openai_api_fields", {}
            ).get("id")
            if not model_key:
                print("Error: No model key specified", file=sys.stderr)
                sys.exit(1)

            success = push_profile_to_api(
                whole_profile, model_key, args.api_url, args.api_token
            )
            if not success:
                sys.exit(1)

        # Output YAML
        output_dict = whole_profile.to_dict()
        output_yaml = yaml.dump(
            output_dict, default_flow_style=False, sort_keys=False, indent=2
        )

        if args.output:
            with open(args.output, "w") as f:
                f.write(output_yaml)
            print(f"Configuration written to: {args.output}", file=sys.stderr)
        else:
            print(output_yaml)

        print("Configuration generation complete!", file=sys.stderr)

    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
