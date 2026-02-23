"""Shared utilities for commands (avoid duplication)."""

import sys
from pathlib import Path
from typing import Any

import yaml

from ..config import Config

# Domain directories for catalog discovery (mirrors gateway logic)
ALLOWED_DOMAINS = frozenset(
    {
        "text_llm",
        "audio",
        "translation",
        "visual",
        "graphics",
        "embedding",
    }
)


def discover_catalog_models(catalog_dir: Path) -> dict[str, Any]:
    """
    Discover and load models from domain-based directory structure.

    Args:
        catalog_dir: Path to config directory containing models/ subdirectory

    Returns:
        Assembled catalog dict with all discovered models

    Raises:
        ValueError: If models directory missing or invalid structure
    """
    models_dir = catalog_dir / "models"

    if not models_dir.exists():
        raise ValueError(
            f"Models directory not found: {models_dir}\n"
            f"Expected domain-based catalog at config/models/"
        )

    model_files: dict[str, Path] = {}

    # Recursively discover *.yaml files
    for yaml_file in models_dir.rglob("*.yaml"):
        # Validate domain
        try:
            rel = yaml_file.relative_to(models_dir)
        except ValueError:
            continue

        if not rel.parts:
            continue

        domain = rel.parts[0]
        if domain not in ALLOWED_DOMAINS:
            raise ValueError(
                f"Invalid catalog file location: {yaml_file}\n"
                f"Expected domain to be one of: {sorted(ALLOWED_DOMAINS)}\n"
                f"Got: {domain}"
            )

        model_id = yaml_file.stem

        # Check for duplicates
        if model_id in model_files:
            raise ValueError(
                f"Duplicate model ID '{model_id}' found:\n"
                f"  - {model_files[model_id]}\n"
                f"  - {yaml_file}\n"
                f"Model IDs must be unique across all catalog files."
            )

        model_files[model_id] = yaml_file

    if not model_files:
        raise ValueError(
            f"No model files found in {models_dir}\n"
            f"Expected *.yaml files under domain directories."
        )

    # Load all models
    models: dict[str, Any] = {}
    for model_id, file_path in model_files.items():
        try:
            with open(file_path) as f:
                model_entry = yaml.safe_load(f)

            if not model_entry:
                raise ValueError(f"Empty model file: {file_path}")

            # Validate required V2 fields
            if "schema" not in model_entry:
                raise ValueError(f"Missing 'schema' field in {file_path}")

            if "metadata" not in model_entry:
                raise ValueError(f"Missing 'metadata' field in {file_path}")

            models[model_id] = model_entry

        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {file_path}: {e}") from e

    # Build catalog structure
    return {
        "schema_version": 2,
        "catalog_version": "1.0",
        "catalog_type": "static",
        "models": models,
    }


def load_catalog_yaml(
    args, config: Config
) -> tuple[dict[str, Any] | None, Path | None]:
    """
    Load catalog from domain-based directory structure.

    Post-Phase-6: Discovers models from config/models/**/*.yaml

    Returns:
        (catalog_dict, catalog_path) on success
        (None, None) on error (prints to stderr)
    """
    catalog_path = _resolve_catalog_path(args, config)
    if not catalog_path:
        return None, None

    catalog_dir = catalog_path.parent

    try:
        catalog = discover_catalog_models(catalog_dir)
        return catalog, catalog_dir
    except Exception as e:
        print(f"❌ Failed to discover catalog: {e}", file=sys.stderr)
        return None, None


def _resolve_catalog_path(args, config: Config) -> Path | None:
    """Resolve catalog path from args or config."""
    if hasattr(args, "catalog_file") and args.catalog_file:
        resolved_path: Path = Path(args.catalog_file)
    else:
        resolved_path: Path | None = config.catalog_path
        if resolved_path is None:
            print("❌ Catalog path not configured", file=sys.stderr)
            return None

    if not resolved_path.exists():
        print(f"❌ Catalog not found: {resolved_path}", file=sys.stderr)
        return None

    return resolved_path
