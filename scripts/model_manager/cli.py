"""
Model Manager - Unified tool for model catalog management.

OFFLINE COMMANDS (no network access):
  discover      Scan directory for uncataloged models
  generate      Generate catalog entry from model file
  analyze       Extract metadata only (no catalog changes)
  list          List models in catalog
  show/info     Show detailed model information
  export        Export model from static to local catalog
  remove        Remove model from local catalog
  update        Update model metadata or profiles
  measure       Measure VRAM/RAM profiles (via Gateway)
  remeasure     Re-measure profiles for multiple models
  init          Initialize local catalog directory
  validate      Validate catalog schema
  lint          Lint catalog for V2 schema compliance
  stats         Display catalog summary statistics

ONLINE COMMANDS (require --network flag):
  download      Download model from HuggingFace
  verify        Verify model against HuggingFace

GLOBAL OPTIONS:
  --offline     Disable all network operations
  --verbose     Verbose output
  --stargate    Stargate URL for federation routing

EXAMPLES:
  # Discover and add new model
  model-manager discover /mnt/torus/models
  model-manager generate /mnt/torus/models/phi-4.gguf --add-to-local
  model-manager measure phi-4-q8-0 --contexts 4096,8192 --gpu

  # Customize existing model
  model-manager export qwen2-5-coder-14b-instruct-q8-0
  model-manager update qwen2-5-coder-14b-instruct-q8-0 --activate-gpu 4096,8192,16384

  # Download new model (network required)
  model-manager download phi-4-q8-0 --network

  # Verify local model (network required)
  model-manager verify /path/to/model.gguf --repo microsoft/phi-4-gguf --network

PRIVACY:
  This tool follows a privacy-first design. Network operations (download, verify)
  require explicit --network flag to acknowledge outbound connections.
  Use --offline to ensure no network operations occur.
"""

import argparse
import importlib
import sys
from pathlib import Path

from .cli_parsers import (
    add_check_resources_parser,
    add_discover_parser,
    add_download_from_catalog_parser,
    add_download_parser,
    add_export_parser,
    add_generate_parser,
    add_info_parser,
    add_init_parser,
    add_lint_parser,
    add_list_parser,
    add_measure_parser,
    add_promote_to_verified_parser,
    add_remeasure_parser,
    add_remove_parser,
    add_stats_parser,
    add_update_parser,
    add_validate_parser,
    add_verify_parser,
)
from .config import (
    DEFAULT_MODEL_ROOT,
    DEFAULT_VERIFIED_PATH,
    Config,
)


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        description="Model Manager - Unified tool for model catalog management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Global options
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Disable all network operations (fail instead of connecting)",
    )
    parser.add_argument(
        "--verified",
        type=Path,
        default=DEFAULT_VERIFIED_PATH,
        help=f"Path to verified_models.json (default: {DEFAULT_VERIFIED_PATH})",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,  # Will be initialized in Config.__post_init__
        help="Path to model_catalog.yaml (default: workspace_root/config/model_catalog.yaml)",
    )
    parser.add_argument(
        "--model-root",
        type=Path,
        default=DEFAULT_MODEL_ROOT,
        help=f"Model root directory (default: {DEFAULT_MODEL_ROOT})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument(
        "--gateway-api-key",
        metavar="KEY",
        help="Gateway API key (default: $GATEWAY_API_KEY)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Request timeout seconds (default: MODEL_MANAGER_TIMEOUT or 10)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_check_resources_parser(subparsers)
    add_discover_parser(subparsers)
    add_generate_parser(subparsers)
    add_verify_parser(subparsers)
    add_promote_to_verified_parser(subparsers)
    add_download_parser(subparsers)
    add_download_from_catalog_parser(subparsers)
    add_list_parser(subparsers)
    add_info_parser(subparsers)
    add_export_parser(subparsers)
    add_remove_parser(subparsers)
    add_init_parser(subparsers)
    add_validate_parser(subparsers)
    add_update_parser(subparsers)
    add_measure_parser(subparsers)
    add_remeasure_parser(subparsers)
    add_lint_parser(subparsers)
    add_stats_parser(subparsers)

    return parser


# Lazy command mapping: command_name -> (module_path, function_name)
# This avoids eager imports of optional dependencies (e.g., HuggingFace tools)
COMMAND_REGISTRY = {
    "check-resources": ("scripts.model_manager.commands.check_resources", "cmd_check_resources"),
    "discover": ("scripts.model_manager.commands.catalog", "cmd_discover"),
    "generate": ("scripts.model_manager.commands.catalog", "cmd_generate"),
    "verify": ("scripts.model_manager.commands.verify", "cmd_verify"),
    "promote-to-verified": ("scripts.model_manager.commands.promote", "cmd_promote_to_verified"),
    "download": ("scripts.model_manager.commands.download", "cmd_download"),
    "download-from-catalog": ("scripts.model_manager.commands.download_catalog", "cmd_download_from_catalog"),
    "list": ("scripts.model_manager.commands.catalog", "cmd_list"),
    "info": ("scripts.model_manager.commands.catalog", "cmd_info"),
    "show": ("scripts.model_manager.commands.catalog", "cmd_info"),
    "export": ("scripts.model_manager.commands.local_catalog", "cmd_export"),
    "remove": ("scripts.model_manager.commands.local_catalog", "cmd_remove"),
    "init": ("scripts.model_manager.commands.local_catalog", "cmd_init"),
    "validate": ("scripts.model_manager.commands.local_catalog", "cmd_validate"),
    "update": ("scripts.model_manager.commands.local_catalog", "cmd_update"),
    "measure": ("scripts.model_manager.commands.measure", "cmd_measure"),
    "remeasure": ("scripts.model_manager.commands.measure", "cmd_remeasure"),
    "lint": ("scripts.model_manager.commands.lint.cmd", "cmd_lint"),
    "stats": ("scripts.model_manager.commands.stats", "cmd_stats"),
}


def _load_command(command_name: str):
    """Lazy-load command handler (avoids importing deps until needed)."""
    if command_name not in COMMAND_REGISTRY:
        return None

    module_path, func_name = COMMAND_REGISTRY[command_name]
    try:
        module = importlib.import_module(module_path)
        return getattr(module, func_name)
    except (ImportError, AttributeError) as e:
        print(f"❌ Failed to load command '{command_name}': {e}", file=sys.stderr)
        return None


def main() -> None:
    """CLI entry point."""
    parser = create_parser()
    args = parser.parse_args()

    config = Config(
        verified_path=args.verified,
        catalog_path=args.catalog,
        model_root=args.model_root,
        verbose=args.verbose,
    )

    handler = _load_command(args.command)
    if handler:
        sys.exit(handler(args, config))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
