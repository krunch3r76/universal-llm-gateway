#!/usr/bin/env python3
"""
Split Monolithic Catalog into Domain-Based Individual Files.

Usage:
    python scripts/split_catalog.py config/model_catalog.yaml [--dry-run]

Safety:
    - Creates a timestamped backup of the source catalog BEFORE writing any files.
    - Backup is stored next to the source catalog and never overwrites an existing backup.

Creates:
    config/models/
    ├── text_llm/llama-cpp/*.yaml
    ├── text_llm/vllm/*.yaml
    ├── audio/whisper/*.yaml
    ├── translation/ctranslate2/*.yaml
    ├── visual/llama-cpp/*.yaml
    └── graphics/diffusers/*.yaml

Strategy:
    - Reads monolithic catalog
    - Extracts each model entry
    - Determines domain and engine from schema
    - Writes individual YAML files
    - Reports statistics
"""

import sys
from pathlib import Path
from typing import Any

import yaml

# Import from gateway package
sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "_universal-llm-gateway" / "src"))

from core.catalog.split import determine_model_path, write_model_file
from catalog_split import create_timestamped_backup  # backup.py stays in scripts


def split_catalog(catalog_path: Path, output_dir: Path, dry_run: bool = False) -> dict[str, Any]:
    """
    Split monolithic catalog into individual files.

    Args:
        catalog_path: Path to model_catalog.yaml
        output_dir: Path to config/models/ directory
        dry_run: If True, print actions without writing files

    Returns:
        Dict with split statistics
    """
    # Backup source catalog (required for safety), unless dry-run
    backup_path = None
    if not dry_run:
        backup_path = create_timestamped_backup(catalog_path)
        print(f"✅ Backup created: {backup_path}")

    # Load catalog
    print(f"Loading catalog: {catalog_path}")
    with open(catalog_path) as f:
        catalog = yaml.safe_load(f)

    models = catalog.get("models", {})
    if not models:
        print("❌ No models found in catalog")
        return {"models_processed": 0, "files_created": 0, "errors": 0, "backup_path": backup_path}

    stats: dict[str, Any] = {
        "models_processed": 0,
        "files_created": 0,
        "errors": 0,
        "domains": {},
        "backup_path": backup_path,
    }

    # Process each model
    for model_id, model_entry in models.items():
        print(f"\nProcessing: {model_id}")

        try:
            # Determine output path
            relative_path = determine_model_path(model_id, model_entry)
            domain_dir = output_dir / relative_path
            output_file = domain_dir / f"{model_id}.yaml"

            # Track stats
            stats["models_processed"] += 1
            stats["domains"][relative_path] = stats["domains"].get(relative_path, 0) + 1

            if dry_run:
                print(f"  → Would write to: {output_file.relative_to(output_dir.parent.parent)}")
                continue

            # Write model file
            write_model_file(output_file, model_entry)
            stats["files_created"] += 1
            print(f"  ✅ Written to: {output_file.relative_to(output_dir.parent.parent)}")

        except Exception as e:
            stats["errors"] += 1
            print(f"  ❌ Failed: {e}")

    return stats


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python split_catalog.py <catalog.yaml> [--dry-run]")
        return 1

    catalog_path = Path(sys.argv[1])
    dry_run = "--dry-run" in sys.argv

    if not catalog_path.exists():
        print(f"❌ Catalog not found: {catalog_path}")
        return 1

    output_dir = catalog_path.parent / "models"

    print("=" * 60)
    print("Catalog Split Utility (Immediate Cutover)")
    print("=" * 60)
    print(f"Source: {catalog_path}")
    print(f"Output: {output_dir}")
    print(f"Mode: {'DRY RUN' if dry_run else 'WRITE'}")
    print()

    stats = split_catalog(catalog_path, output_dir, dry_run=dry_run)

    print("\n" + "=" * 60)
    print("Split Summary")
    print("=" * 60)
    print(f"Models processed: {stats['models_processed']}")
    print(f"Files created: {stats['files_created']}")
    print(f"Errors: {stats['errors']}")
    print("\nBy domain:")
    for domain, count in sorted(stats["domains"].items()):
        print(f"  {domain}: {count} models")

    if dry_run:
        print("\n⚠️  DRY RUN - No files written. Run without --dry-run to apply.")
        return 0

    if stats["errors"] > 0:
        print(f"\n❌ {stats['errors']} errors occurred. Check output above.")
        return 1

    if stats["files_created"] != stats["models_processed"]:
        print(f"\n❌ Mismatch: processed {stats['models_processed']} but only created {stats['files_created']} files")
        return 1

    print(f"\n✅ Successfully split {stats['files_created']} models into individual files")
    print(f"   Backup at: {stats['backup_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
