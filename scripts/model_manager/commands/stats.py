"""Catalog statistics command."""

import argparse
from typing import Any

from ..config import Config
from ._shared import load_catalog_yaml


def cmd_stats(args: argparse.Namespace, config: Config) -> int:
    """Display catalog summary statistics."""
    catalog, catalog_path = load_catalog_yaml(args, config)
    if not catalog or not catalog_path:
        return 1

    # Type guard: models must be dict
    models = catalog.get("models", {})
    if not isinstance(models, dict):
        print("❌ Invalid catalog: 'models' must be dict")
        return 1

    stats = _collect_stats(models)
    _print_stats(catalog, catalog_path.name, stats)

    return 0


def _collect_stats(models: dict[str, Any]) -> dict[str, Any]:
    """Collect statistics from models (pure function)."""
    schema_counts: dict[str, int] = {}
    format_counts: dict[str, int] = {}
    device_counts: dict[str, int] = {}
    models_with_profiles = 0

    for entry in models.values():
        if not isinstance(entry, dict):
            continue

        # Count schemas
        schema = entry.get("schema", "unknown")
        schema_counts[schema] = schema_counts.get(schema, 0) + 1

        # Count formats
        metadata = entry.get("metadata", {})
        if isinstance(metadata, dict):
            model_format = metadata.get("format", "unknown")
            format_counts[model_format] = format_counts.get(model_format, 0) + 1

        # Count devices
        devices = entry.get("devices", {})
        if isinstance(devices, dict):
            for device in devices:
                device_counts[device] = device_counts.get(device, 0) + 1

            # Check for profiles
            has_profiles = any(
                isinstance(d, dict) and d.get("profiles") for d in devices.values()
            )
            if has_profiles:
                models_with_profiles += 1

    return {
        "schema_counts": schema_counts,
        "format_counts": format_counts,
        "device_counts": device_counts,
        "models_with_profiles": models_with_profiles,
    }


def _print_stats(catalog: dict, catalog_name: str, stats: dict[str, Any]) -> None:
    """Print statistics summary."""
    print(f"\n{'=' * 60}")
    print(f"Catalog: {catalog_name}")
    print(f"{'=' * 60}\n")

    print(f"Schema Version: {catalog.get('schema_version', 'unknown')}")
    print(f"Total Models: {len(catalog.get('models', {}))}")
    print(f"Models with Profiles: {stats['models_with_profiles']}")

    print("\nBy Schema:")
    for schema, count in sorted(stats["schema_counts"].items()):
        print(f"  {schema}: {count}")

    print("\nBy Format:")
    for fmt, count in sorted(stats["format_counts"].items()):
        print(f"  {fmt}: {count}")

    print("\nDevice Configurations:")
    for device, count in sorted(stats["device_counts"].items()):
        print(f"  {device}: {count}")

    print()
