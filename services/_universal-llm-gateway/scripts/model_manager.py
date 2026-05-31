#!/usr/bin/env python3
"""
Model Configuration Manager CLI

Command-line interface for managing model configurations in model_loaders.yaml.
Provides add, update, get, and example operations with schema validation.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.core.config_manager import ConfigManager, ConfigValidationError  # noqa: E402
from src.tools.discovery import DiscoveryError, ModelDiscovery  # noqa: E402


def format_error(message: str) -> str:
    """Format error message with color"""
    return f"\033[91m❌ Error:\033[0m {message}"


def format_success(message: str) -> str:
    """Format success message with color"""
    return f"\033[92m✅ Success:\033[0m {message}"


def format_info(message: str) -> str:
    """Format info message with color"""
    return f"\033[94mℹ️  Info:\033[0m {message}"


def load_json_config(file_path: str) -> dict[str, Any]:
    """
    Load model configuration from JSON file.

    Args:
        file_path: Path to JSON configuration file

    Returns:
        Configuration dictionary

    Raises:
        SystemExit: If file cannot be loaded
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        print(format_error(f"File not found: {file_path}"), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(format_error(f"Invalid JSON in {file_path}: {e}"), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(format_error(f"Failed to load {file_path}: {e}"), file=sys.stderr)
        sys.exit(1)


def cmd_add(args, config_manager: ConfigManager):
    """Add a new model to configuration"""
    # Load model config from JSON file
    model_config = load_json_config(args.config_file)

    # Extract model key from args or infer from config
    if args.key:
        model_key = args.key
    else:
        # Try to infer from openai_api_fields.id
        model_key = model_config.get("info", {}).get("openai_api_fields", {}).get("id")

        if not model_key:
            print(
                format_error(
                    "Could not infer model key from config. "
                    "Please specify --key MODEL_KEY"
                ),
                file=sys.stderr,
            )
            sys.exit(1)

        print(format_info(f"Inferred model key: {model_key}"))

    # Add model
    try:
        config_manager.upsert_model(
            model_key=model_key,
            model_config=model_config,
            allow_key_overwrite=args.force,
        )

        print(format_success(f"Model '{model_key}' added to configuration"))
        print(format_info(f"Config file: {config_manager.config_path}"))

        if not args.no_reload_hint:
            print(format_info("Restart gateway to load the new model"))

    except ConfigValidationError as e:
        print(format_error(f"Validation failed:\n{e}"), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(format_error(f"Failed to add model: {e}"), file=sys.stderr)
        sys.exit(1)


def cmd_update(args, config_manager: ConfigManager):
    """Update an existing model configuration"""
    model_key = args.model_key

    # Load new model config from JSON file
    model_config = load_json_config(args.config_file)

    # Update model
    try:
        config_manager.upsert_model(
            model_key=model_key, model_config=model_config, allow_key_overwrite=True
        )

        print(format_success(f"Model '{model_key}' updated in configuration"))
        print(format_info(f"Config file: {config_manager.config_path}"))

        if not args.no_reload_hint:
            print(format_info("Restart gateway or call reload API to apply changes"))

    except ConfigValidationError as e:
        print(format_error(f"Validation failed:\n{e}"), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(format_error(f"Failed to update model: {e}"), file=sys.stderr)
        sys.exit(1)


def cmd_get(args, config_manager: ConfigManager):
    """Get current model configuration"""
    model_key = args.model_key

    try:
        model_config = config_manager.get_model(model_key)

        if model_config is None:
            print(format_error(f"Model '{model_key}' not found"), file=sys.stderr)
            sys.exit(1)

        # Output format
        if args.format == "json":
            print(json.dumps(model_config, indent=2))
        elif args.format == "yaml":
            print(yaml.dump(model_config, default_flow_style=False, sort_keys=False))
        else:
            # Pretty print
            print(f"Model: {model_key}")
            print("=" * 60)
            print(yaml.dump(model_config, default_flow_style=False, sort_keys=False))

    except Exception as e:
        print(format_error(f"Failed to get model: {e}"), file=sys.stderr)
        sys.exit(1)


def cmd_list(args, config_manager: ConfigManager):
    """List all models in configuration"""
    try:
        models = config_manager.list_models()

        if args.format == "json":
            # Output as JSON
            model_list = []
            for key, config in models.items():
                if isinstance(config, dict):
                    info = config.get("info", {})
                    model_list.append(
                        {
                            "key": key,
                            "name": info.get("name", key),
                            "format": info.get("format", "unknown"),
                            "enabled": info.get("enabled", False),
                            "openai_id": info.get("openai_api_fields", {}).get(
                                "id", key
                            ),
                        }
                    )
            print(json.dumps(model_list, indent=2))
        else:
            # Pretty table output
            print(f"Models in {config_manager.config_path}:")
            print("=" * 80)
            print(f"{'Key':<30} {'Format':<10} {'Enabled':<10} {'OpenAI ID':<30}")
            print("-" * 80)

            for key, config in models.items():
                if isinstance(config, dict):
                    info = config.get("info", {})
                    format_type = info.get("format", "unknown")
                    enabled = "✓" if info.get("enabled", False) else "✗"
                    openai_id = info.get("openai_api_fields", {}).get("id", key)
                    print(f"{key:<30} {format_type:<10} {enabled:<10} {openai_id:<30}")
                else:
                    # Alias
                    print(f"{key:<30} {'alias':<10} {'-':<10} {config}")

            print("-" * 80)
            print(f"Total: {len(models)} entries")

    except Exception as e:
        print(format_error(f"Failed to list models: {e}"), file=sys.stderr)
        sys.exit(1)


def cmd_delete(args, config_manager: ConfigManager):
    """Delete a model from configuration"""
    model_key = args.model_key

    # Confirm deletion unless --force
    if not args.force:
        response = input(f"Delete model '{model_key}'? [y/N]: ")
        if response.lower() not in ["y", "yes"]:
            print("Cancelled")
            return

    try:
        config_manager.delete_model(model_key)
        print(format_success(f"Model '{model_key}' deleted from configuration"))

        if not args.no_reload_hint:
            print(format_info("Restart gateway to apply changes"))

    except ConfigValidationError as e:
        print(format_error(str(e)), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(format_error(f"Failed to delete model: {e}"), file=sys.stderr)
        sys.exit(1)


def cmd_example(args, config_manager: ConfigManager):
    """Get example configuration for a format"""
    format_type = args.format

    try:
        example = config_manager.get_example(format_type)

        # Output format
        if args.output_format == "json":
            print(json.dumps(example, indent=2))
        else:  # yaml
            print(f"# Example {format_type.upper()} model configuration")
            print("# Copy this structure and replace with your model's values")
            print()
            print(yaml.dump(example, default_flow_style=False, sort_keys=False))

        # Show schema info if requested
        if args.with_schema_info:
            print("\n# Schema Information", file=sys.stderr)
            print("# " + "=" * 60, file=sys.stderr)
            schema_info = config_manager.get_schema_info(format_type)
            print(
                f"# Required fields: {len(schema_info['required_fields'])}",
                file=sys.stderr,
            )
            print(
                f"# Optional fields: {len(schema_info['optional_fields'])}",
                file=sys.stderr,
            )

    except ValueError as e:
        print(format_error(str(e)), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(format_error(f"Failed to generate example: {e}"), file=sys.stderr)
        sys.exit(1)


def cmd_validate(args, config_manager: ConfigManager):
    """Validate configuration file"""
    try:
        config_manager.load_and_validate()
        print(format_success("Configuration is valid"))

        # Show stats
        models = config_manager.list_models()
        model_count = len([k for k, v in models.items() if isinstance(v, dict)])
        print(format_info(f"Found {model_count} model configurations"))

    except ConfigValidationError as e:
        print(format_error(f"Validation failed:\n{e}"), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(format_error(f"Validation error: {e}"), file=sys.stderr)
        sys.exit(1)


def cmd_discover(args, config_manager: ConfigManager):
    """Discover model configuration from file"""
    model_path = args.path

    try:
        # Initialize discovery
        discovery = ModelDiscovery()

        if not discovery.djinn_available:
            print(
                format_error(
                    "inference_djinn not available. "
                    "Install it to use discovery features."
                ),
                file=sys.stderr,
            )
            sys.exit(1)

        print(format_info(f"Discovering model at: {model_path}"))

        # Discover model config
        if args.format:
            # Use specific format
            if args.format == "gguf":
                model_config = discovery.discover_gguf(model_path)
            else:
                model_config = discovery.discover_hf(
                    model_path, format_hint=args.format
                )
        else:
            # Auto-detect format
            model_config = discovery.discover_auto(model_path)

        # Get model key from args or infer
        if args.key:
            model_key = args.key
        else:
            model_key = model_config["info"]["openai_api_fields"]["id"]
            print(format_info(f"Inferred model key: {model_key}"))

        # Output discovered config
        if args.output_format == "json":
            print(json.dumps(model_config, indent=2))
        else:
            print(yaml.dump(model_config, default_flow_style=False, sort_keys=False))

        # Add to config if requested
        if args.add:
            print()
            print(format_info(f"Adding model '{model_key}' to configuration..."))

            config_manager.upsert_model(
                model_key=model_key,
                model_config=model_config,
                allow_key_overwrite=args.force,
            )

            print(format_success(f"Model '{model_key}' added to configuration"))
            print(format_info(f"Config file: {config_manager.config_path}"))

            if not args.no_reload_hint:
                print(format_info("Restart gateway to load the new model"))

    except DiscoveryError as e:
        print(format_error(f"Discovery failed: {e}"), file=sys.stderr)
        sys.exit(1)
    except ConfigValidationError as e:
        print(format_error(f"Validation failed:\n{e}"), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(format_error(f"Error during discovery: {e}"), file=sys.stderr)
        import traceback

        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Model Configuration Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Get example GGUF configuration
  %(prog)s example --format gguf
  
  # Add new model from JSON file
  %(prog)s add --config-file my_model.json --key my-model-id
  
  # Update existing model
  %(prog)s update my-model-id --config-file updated_model.json
  
  # Get current model configuration
  %(prog)s get my-model-id
  
  # List all models
  %(prog)s list
  
  # Delete a model
  %(prog)s delete my-model-id
  
  # Validate configuration
  %(prog)s validate
  
  # Discover model from file
  %(prog)s discover /path/to/model.gguf
  
  # Discover and add in one step
  %(prog)s discover /path/to/model.gguf --add --key my-model
        """,
    )

    parser.add_argument(
        "--config",
        default="config/model_loaders.yaml",
        help="Path to model_loaders.yaml (default: config/model_loaders.yaml)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    subparsers.required = True

    # Add command
    parser_add = subparsers.add_parser("add", help="Add new model")
    parser_add.add_argument(
        "--config-file", required=True, help="JSON file with model configuration"
    )
    parser_add.add_argument(
        "--key", help="Model key (inferred from config if not provided)"
    )
    parser_add.add_argument(
        "--force", action="store_true", help="Overwrite if model exists"
    )
    parser_add.add_argument(
        "--no-reload-hint", action="store_true", help="Suppress reload hint"
    )
    parser_add.set_defaults(func=cmd_add)

    # Update command
    parser_update = subparsers.add_parser("update", help="Update existing model")
    parser_update.add_argument("model_key", help="Model key to update")
    parser_update.add_argument(
        "--config-file", required=True, help="JSON file with new configuration"
    )
    parser_update.add_argument(
        "--no-reload-hint", action="store_true", help="Suppress reload hint"
    )
    parser_update.set_defaults(func=cmd_update)

    # Get command
    parser_get = subparsers.add_parser("get", help="Get model configuration")
    parser_get.add_argument("model_key", help="Model key to retrieve")
    parser_get.add_argument(
        "--format",
        choices=["json", "yaml", "pretty"],
        default="pretty",
        help="Output format (default: pretty)",
    )
    parser_get.set_defaults(func=cmd_get)

    # List command
    parser_list = subparsers.add_parser("list", help="List all models")
    parser_list.add_argument(
        "--format",
        choices=["json", "table"],
        default="table",
        help="Output format (default: table)",
    )
    parser_list.set_defaults(func=cmd_list)

    # Delete command
    parser_delete = subparsers.add_parser("delete", help="Delete model")
    parser_delete.add_argument("model_key", help="Model key to delete")
    parser_delete.add_argument("--force", action="store_true", help="Skip confirmation")
    parser_delete.add_argument(
        "--no-reload-hint", action="store_true", help="Suppress reload hint"
    )
    parser_delete.set_defaults(func=cmd_delete)

    # Example command
    parser_example = subparsers.add_parser("example", help="Get example configuration")
    parser_example.add_argument(
        "--format", choices=["gguf", "hf"], required=True, help="Model format"
    )
    parser_example.add_argument(
        "--output-format",
        choices=["json", "yaml"],
        default="yaml",
        help="Output format (default: yaml)",
    )
    parser_example.add_argument(
        "--with-schema-info",
        action="store_true",
        help="Include schema field information",
    )
    parser_example.set_defaults(func=cmd_example)

    # Validate command
    parser_validate = subparsers.add_parser("validate", help="Validate configuration")
    parser_validate.set_defaults(func=cmd_validate)

    # Discover command
    parser_discover = subparsers.add_parser("discover", help="Discover model from file")
    parser_discover.add_argument("path", help="Path to model file or directory")
    parser_discover.add_argument(
        "--format",
        choices=["gguf", "hf", "gptq", "awq"],
        help="Model format (auto-detected if not specified)",
    )
    parser_discover.add_argument("--key", help="Model key (inferred if not provided)")
    parser_discover.add_argument(
        "--add", action="store_true", help="Add discovered model to configuration"
    )
    parser_discover.add_argument(
        "--force",
        action="store_true",
        help="Overwrite if model exists (use with --add)",
    )
    parser_discover.add_argument(
        "--output-format",
        choices=["json", "yaml"],
        default="yaml",
        help="Output format (default: yaml)",
    )
    parser_discover.add_argument(
        "--no-reload-hint", action="store_true", help="Suppress reload hint"
    )
    parser_discover.set_defaults(func=cmd_discover)

    args = parser.parse_args()

    # Initialize config manager
    try:
        config_manager = ConfigManager(args.config)
    except Exception as e:
        print(
            format_error(f"Failed to initialize config manager: {e}"), file=sys.stderr
        )
        sys.exit(1)

    # Execute command
    args.func(args, config_manager)


if __name__ == "__main__":
    main()
