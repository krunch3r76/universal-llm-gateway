"""Generate command helpers for model catalog entries."""

import argparse
import sys
from pathlib import Path

from universal_logging import get_logger

from ..utils import load_yaml, save_yaml

logger = get_logger(__name__)


def generate_via_api(
    args: argparse.Namespace,
    entries: dict,
    stargate_url: str,
    model_path: Path | str | None = None,
) -> int:
    """
    Handle catalog entry generation — dual-write to static and local catalogs.

    Static catalog: writes metadata-only to config/models/ (no API, --stargate used for reload)
    Local catalog: writes skeleton (metadata + empty devices) to ~/.gateway/catalog/

    Args:
        args: Command arguments
        entries: Dict of model_id -> catalog entry
        stargate_url: Stargate URL (e.g., http://localhost:9999)
        model_path: Optional model path for context
    """
    import requests

    from .catalog_writer import write_local_catalog_entry, write_static_catalog_entry

    failed = 0
    for model_key, catalog_entry in entries.items():
        try:
            static_path, static_op = write_static_catalog_entry(
                model_key, catalog_entry, allow_overwrite=True
            )
            local_path, local_op = write_local_catalog_entry(
                model_key, catalog_entry, allow_overwrite=True
            )
            print(
                f"✅ {model_key} "
                f"(static {static_op}: {static_path}, local {local_op}: {local_path})"
            )
        except Exception as e:
            logger.error(f"Failed to write catalog for {model_key}: {e}")
            print(f"❌ {model_key}: {e}", file=sys.stderr)
            failed += 1

    if failed:
        print(f"\n{failed}/{len(entries)} model(s) failed", file=sys.stderr)
        return 1

    print(f"\n{len(entries)} model(s) written to static + local catalog")

    if stargate_url:
        print("Reloading Gateway catalog...")
        try:
            response = requests.post(
                f"{stargate_url}/gateway/catalog/reload",
                timeout=10,
            )
            response.raise_for_status()
            print("✅ Gateway catalog reloaded")
        except requests.RequestException as e:
            logger.warning(f"Failed to reload Gateway catalog: {e}")
            print(
                "⚠️  Catalog reload failed - restart services to see updates",
                file=sys.stderr,
            )

    return 0


def generate_to_file(args: argparse.Namespace, entries: dict, output_path: Path) -> int:
    """Write generated entries to file."""
    output_path = output_path.expanduser()

    if getattr(args, "append", False) and output_path.exists():
        existing = load_yaml(output_path)
        existing.setdefault("models", {}).update(entries)
        from inference_djinn.catalog.schema import ensure_catalog_version

        ensure_catalog_version(existing)
        save_yaml(output_path, existing)
        print(f"Appended {len(entries)} model(s) to {output_path}")
    else:
        from inference_djinn.catalog.schema import get_catalog_template

        catalog = get_catalog_template()
        catalog["models"] = entries
        output_path.parent.mkdir(parents=True, exist_ok=True)
        save_yaml(output_path, catalog)
        print(f"Saved {len(entries)} model(s) to {output_path}")

    return 0
