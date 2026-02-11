"""Command-line argument parsers for model manager commands."""

import argparse
from pathlib import Path


def add_discover_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add discover command parser."""
    p = subparsers.add_parser("discover", help="Scan directory for uncataloged models")
    p.add_argument("path", type=Path, help="Directory to scan")
    p.add_argument(
        "--no-recursive", action="store_true", help="Don't scan subdirectories"
    )
    p.add_argument(
        "--include-cataloged",
        action="store_true",
        help="Include already cataloged models",
    )


def add_generate_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add generate command parser."""
    p = subparsers.add_parser("generate", help="Generate catalog entry from model file")
    p.add_argument("path", type=Path, help="Model file or directory")
    p.add_argument("--repo", help="HuggingFace repo ID for verification (optional)")
    p.add_argument(
        "--file",
        help="Filename in HF repo (defaults to model filename for single files)",
    )
    p.add_argument(
        "--no-trace", action="store_true", help="Skip auto-tracing HuggingFace source"
    )
    p.add_argument(
        "--no-recursive", action="store_true", help="Don't scan subdirectories"
    )
    p.add_argument("-o", "--output", type=Path, help="Output file (YAML)")
    p.add_argument(
        "--append", action="store_true", help="Append to existing catalog file"
    )

    # Stargate API destination (mutually exclusive with output)
    p.add_argument(
        "--stargate",
        metavar="URL",
        help="Stargate URL for federated access (default: http://localhost:9999)",
    )

    p.add_argument(
        "--static",
        action="store_true",
        help="Write to static catalog (maintainer mode); default is dynamic",
    )
    p.add_argument(
        "--add-verified",
        action="store_true",
        help="Also add to verified registry after catalog generation",
    )
    p.add_argument(
        "--network",
        action="store_true",
        help="Allow network access to HuggingFace (for --add-verified sha256/size)",
    )
    p.add_argument(
        "--force-verified",
        action="store_true",
        help="Overwrite existing verified registry entry",
    )

    # Vision model options
    vision_group = p.add_argument_group("Vision Model Options")
    vision_group.add_argument(
        "--mmproj",
        metavar="PATH",
        help="Path to mmproj/CLIP file for vision models",
    )
    vision_group.add_argument(
        "--vision-architecture",
        metavar="ARCH",
        choices=["qwen2_vl", "llava_1_5", "llava_1_6", "minicpm_v", "moondream", "mistral3"],
        help="Vision architecture (required with --mmproj)",
    )
    vision_group.add_argument(
        "--tokens-per-image",
        type=int,
        metavar="N",
        help="Tokens per image (required with --mmproj)",
    )


def add_verify_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add verify command parser."""
    p = subparsers.add_parser("verify", help="Verify model origin against HuggingFace")
    p.add_argument("path", type=Path, help="Local model file or directory")
    p.add_argument(
        "--repo", required=True, help="HuggingFace repo ID (e.g., user/repo)"
    )
    p.add_argument(
        "--file",
        help="Filename in HF repo (defaults to local filename for GGUF files)",
    )
    p.add_argument(
        "--network",
        action="store_true",
        help="REQUIRED: Acknowledge network access to HuggingFace",
    )


def add_promote_to_verified_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add promote-to-verified command parser."""
    p = subparsers.add_parser(
        "promote-to-verified",
        help="Promote catalog model to verified registry for download support",
        description="""
Promote a model from the catalog to the verified registry.

Fetches model metadata from Gateway catalog API and adds to verified_models.json,
enabling the model for download via 'model-manager download'.

Requires --network if sha256/size need to be fetched from HuggingFace.
        """,
    )
    p.add_argument("model_id", help="Model ID to promote")
    p.add_argument(
        "--network",
        action="store_true",
        help="Allow network access to HuggingFace for metadata",
    )
    p.add_argument(
        "--refresh-hf",
        action="store_true",
        help="Re-fetch sha256/size from HuggingFace (requires --network)",
    )
    p.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite existing verified registry entry",
    )


def add_download_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add download command parser."""
    p = subparsers.add_parser("download", help="Download model from verified registry")
    p.add_argument("model_id", help="Model ID to download")
    p.add_argument("--dest", type=Path, help="Destination directory")
    p.add_argument("--no-verify", action="store_true", help="Skip SHA256 verification")
    p.add_argument(
        "--network",
        action="store_true",
        help="REQUIRED: Acknowledge network access to HuggingFace",
    )


def add_download_from_catalog_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add download-from-catalog command parser."""
    p = subparsers.add_parser(
        "download-from-catalog",
        help="Download model from static catalog (supports GGUF and vLLM formats)",
    )
    p.add_argument("model_id", help="Model ID from catalog to download")
    p.add_argument("--dest", type=Path, help="Destination directory (default: MODEL_PATH_ROOT)")
    p.add_argument(
        "--ignore-patterns",
        nargs="+",
        default=["*.md", "*.txt"],
        help="File patterns to ignore when downloading directory-based models (default: *.md *.txt)",
    )
    p.add_argument(
        "--network",
        action="store_true",
        help="REQUIRED: Acknowledge network access to HuggingFace",
    )


def add_list_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add list command parser."""
    p = subparsers.add_parser("list", help="List models in catalog")
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--local", action="store_true", help="List local catalog models only"
    )
    group.add_argument(
        "--static", action="store_true", help="List static catalog models only"
    )
    group.add_argument(
        "--merged", action="store_true", help="List merged catalog (default)"
    )
    group.add_argument(
        "--show-verified", action="store_true", help="List verified models registry"
    )
    p.add_argument(
        "--format", choices=["gguf", "hf", "awq", "gptq"], help="Filter by format"
    )


def add_info_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add info command parser."""
    p = subparsers.add_parser("info", aliases=["show"], help="Show model information")
    p.add_argument("model_id", help="Model ID")
    p.add_argument(
        "--local", action="store_true", help="Force file-based catalog (skip Gateway)"
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="Show complete catalog entry including all configurations and profiles",
    )


def add_export_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add export command parser."""
    p = subparsers.add_parser(
        "export", help="Export model from static to local catalog"
    )
    p.add_argument("model_id", help="Model ID to export")
    p.add_argument(
        "--force", "-f", action="store_true", help="Overwrite existing local entry"
    )


def add_remove_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add remove command parser."""
    p = subparsers.add_parser("remove", help="Remove model from local catalog")
    p.add_argument("model_id", help="Model ID to remove")
    p.add_argument("--force", "-f", action="store_true", help="Skip confirmation")


def add_init_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add init command parser."""
    p = subparsers.add_parser("init", help="Initialize local catalog directory")
    p.add_argument("--force", "-f", action="store_true", help="Reinitialize if exists")


def add_validate_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add validate command parser."""
    p = subparsers.add_parser("validate", help="Validate catalog schema")
    p.add_argument("--local", action="store_true", help="Validate local catalog only")
    p.add_argument("--static", action="store_true", help="Validate static catalog only")


def add_update_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add update command parser."""
    p = subparsers.add_parser("update", help="Update model in local catalog")
    p.add_argument("model_id", help="Model ID to update")
    p.add_argument(
        "--set-vram", metavar="CTX:MB", help="Set VRAM for context (e.g., 8192:12500)"
    )
    p.add_argument(
        "--set-ram", metavar="CTX:MB", help="Set RAM for context (e.g., 8192:4000)"
    )
    p.add_argument(
        "--activate-gpu",
        metavar="CONTEXTS",
        help="Set activated GPU contexts (e.g., 4096,8192)",
    )
    p.add_argument(
        "--activate-cpu",
        metavar="CONTEXTS",
        help="Set activated CPU contexts (e.g., 4096,8192)",
    )
    p.add_argument("--set-metadata", metavar="KEY=VALUE", help="Set metadata field")


def add_measure_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add measure command parser."""
    p = subparsers.add_parser(
        "measure",
        help="Measure VRAM/RAM profiles for a model via Gateway Job API",
        description="""
Measure VRAM/RAM profiles for a model.

Default behavior (GPU mode with hybrid offloading):
  - Measures GPU mode with partial offloading support
  - Steps down from training context until it fits on GPU
  - Use --cpu flag for CPU-only measurement

Gateway selection:
  - Queries Stargate for all gateways
  - Selects gateway with model available and most VRAM (GPU) or RAM (CPU)
  - Use --gateway to override with explicit gateway URL

Smart context detection:
  - Reads training_context_length from model metadata
  - GPU mode: steps down from training context until it fits
  - CPU mode: uses training context length

Resource caps:
  - Use --vram-cap and --ram-cap to simulate smaller hardware
  - Contexts exceeding caps will be marked as "doesn't fit"
        """,
    )
    p.add_argument("model_id", help="Model ID to measure")
    p.add_argument(
        "--contexts",
        metavar="CONTEXTS",
        help="Explicit context lengths (comma-separated). Default: auto-detect",
    )
    p.add_argument(
        "--gpu",
        action="store_true",
        help="[Deprecated] GPU mode is now the default. This flag is ignored.",
    )
    p.add_argument(
        "--cpu", action="store_true", help="CPU-only mode (default: GPU with hybrid offload)"
    )
    p.add_argument(
        "--gpu-index", type=int, default=0, help="GPU index to use (default: 0)"
    )
    p.add_argument(
        "--vram-cap",
        type=int,
        metavar="GB",
        help="Max VRAM in GiB (e.g., --vram-cap 24 for 24GB limit)",
    )
    p.add_argument(
        "--ram-cap",
        type=int,
        metavar="GB",
        help="Max RAM in GiB (e.g., --ram-cap 48 for 48GB limit)",
    )
    p.add_argument(
        "--stargate",
        metavar="URL",
        help="Stargate URL for federation routing (default: http://localhost:9999)",
    )
    p.add_argument(
        "--disable-hybrid",
        dest="enable_hybrid",
        action="store_false",
        help="Disable partial GPU offload fallback",
    )

    # Vision model support
    p.add_argument(
        "--mmproj",
        metavar="PATH",
        help="Path to mmproj/CLIP file for vision models (e.g., mmproj-F16.gguf)",
    )
    p.add_argument(
        "--vision-architecture",
        metavar="ARCH",
        help="Vision architecture (e.g., minicpm_v, qwen2_vl, llava_1_5, llava_1_6, moondream). Auto-detected if not provided.",
    )
    p.add_argument(
        "--tokens-per-image",
        type=int,
        metavar="N",
        help=(
            "Tokens per image for vision models (default: architecture default). "
            "Override for specific CLIP quantization or empirical measurement."
        ),
    )

    # Measurement behavior
    p.add_argument(
        "--safety-margin",
        type=int,
        metavar="N",
        help=(
            "Safety margin for hybrid mode binary search (default: 2). "
            "Applied as -N layers from max found. Set to 0 to use max layers. "
            "Example: --safety-margin 0 (no margin), --safety-margin 1 (-1 layer)"
        ),
    )

    # Catalog update
    p.add_argument(
        "--update-catalog",
        action="store_true",
        help="Automatically update catalog with measurement results after completion",
    )
    p.add_argument(
        "--static",
        action="store_true",
        help="Update static catalog (maintainer mode); default is dynamic",
    )


def add_check_resources_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add check-resources command parser."""
    p = subparsers.add_parser(
        "check-resources",
        help="Check system resources and show measurement safety diagnostics",
        description="""
Check system RAM/swap and show recommendations for safe measurement.

Displays:
  - Total and available RAM/swap
  - Current headroom configuration
  - Recommended headroom (to keep SSH/system responsive)
  - Safe memory limit per measurement probe
  - Warnings about low memory or missing swap

Use this before running large model measurements to avoid host freezes.
        """,
    )
    p.add_argument(
        "--suggest-env",
        action="store_true",
        help="Output environment variable settings for recommended configuration",
    )


def add_remeasure_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add remeasure command parser."""
    p = subparsers.add_parser(
        "remeasure",
        help="Re-measure profiles for existing models",
        description="""
Re-measure profiles for models in the catalog.

By default measures:
  - GGUF (llama-cpp-python): GPU + CPU modes
  - vLLM (hf/awq/gptq): GPU only (no CPU/hybrid support)

Use --gguf-only to skip vLLM models.

Gateway selection (default):
  - Queries Stargate for all gateways
  - For each model, selects gateway with model available and most VRAM
  - Use --gateway to override with explicit gateway URL

Uses smart context detection by default:
  - Reads training_context_length from each model's metadata
  - GPU mode: steps down from training context until it fits
  - CPU mode: uses training context length
  - Auto mode: tries GPU first, falls back to CPU

Use --contexts to override with explicit values.
        """,
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--model", help="Specific model ID to remeasure")
    group.add_argument("--all", action="store_true", help="Remeasure all models")
    p.add_argument(
        "--gguf-only",
        action="store_true",
        help="Only remeasure GGUF models (skip vLLM/hf/awq/gptq)",
    )
    p.add_argument(
        "--contexts",
        metavar="CONTEXTS",
        help="Override context lengths (comma-separated). Default: auto-detect",
    )
    p.add_argument(
        "--gpu", action="store_true", help="GPU mode only (step down until fits)"
    )
    p.add_argument("--cpu", action="store_true", help="CPU mode only (GGUF only)")
    p.add_argument(
        "--vram-cap",
        type=int,
        metavar="GB",
        help="Max VRAM in GiB (e.g., --vram-cap 24 for 24GB limit)",
    )
    p.add_argument(
        "--ram-cap",
        type=int,
        metavar="GB",
        help="Max RAM in GiB (e.g., --ram-cap 48 for 48GB limit)",
    )
    p.add_argument(
        "--stargate",
        metavar="URL",
        help="Stargate URL for federation routing (default: http://localhost:9999)",
    )
    p.add_argument(
        "--local", action="store_true", help="Force file-based catalog (skip Gateway)"
    )
    p.add_argument(
        "--disable-hybrid",
        dest="enable_hybrid",
        action="store_false",
        help="Disable partial GPU offload fallback",
    )
    p.add_argument(
        "--mmproj",
        metavar="PATH",
        help="Path to mmproj/CLIP file for vision models (e.g., mmproj-F16.gguf)",
    )
    p.add_argument(
        "--vision-architecture",
        metavar="ARCH",
        help="Vision architecture (e.g., minicpm_v, qwen2_vl, llava_1_5, llava_1_6, moondream). Auto-detected if not provided.",
    )
    p.add_argument(
        "--tokens-per-image",
        type=int,
        metavar="N",
        help=(
            "Tokens per image for vision models (default: architecture default). "
            "Override for specific CLIP quantization or empirical measurement."
        ),
    )


def add_lint_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add lint subcommand."""
    parser = subparsers.add_parser(
        "lint",
        help="Lint catalog for V2 schema compliance",
    )
    parser.add_argument(
        "--catalog", "-c",
        dest="catalog_file",
        help="Path to catalog YAML file",
    )


def add_stats_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add stats subcommand."""
    parser = subparsers.add_parser(
        "stats",
        help="Display catalog summary statistics",
    )
    parser.add_argument(
        "--catalog", "-c",
        dest="catalog_file",
        help="Path to catalog YAML file",
    )
