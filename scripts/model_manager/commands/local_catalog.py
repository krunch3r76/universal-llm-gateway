"""Local catalog management commands."""

import argparse
import sys

import yaml

from ..config import Config


def cmd_export(args: argparse.Namespace, config: Config) -> int:
    """Export model from static to local catalog for customization."""
    try:
        from inference_djinn.catalog.local_config import (
            CatalogConfigError,
            SchemaVersionError,
            export_model_to_local,
        )
    except ImportError:
        print("Error: inference_djinn not available", file=sys.stderr)
        return 1

    try:
        static_path = config.catalog_path if config.catalog_path.exists() else None
        catalog_path = export_model_to_local(
            args.model_id,
            static_catalog_path=static_path,
            force=args.force,
        )
        print(f"✅ Exported '{args.model_id}' to {catalog_path}")
        print("   Edit this file to customize activated_gpu_contexts, resources, etc.")
        return 0
    except SchemaVersionError as e:
        print(f"❌ Schema version error: {e}", file=sys.stderr)
        return 1
    except CatalogConfigError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1


def cmd_remove(args: argparse.Namespace, config: Config) -> int:
    """Remove model from local catalog."""
    try:
        from inference_djinn.catalog.local_config import (
            CatalogConfigError,
            remove_model_from_local,
        )
    except ImportError:
        print("Error: inference_djinn not available", file=sys.stderr)
        return 1

    if not args.force:
        confirm = input(f"Remove '{args.model_id}' from local catalog? [y/N]: ")
        if confirm.lower() != "y":
            print("Cancelled")
            return 0

    try:
        result = remove_model_from_local(args.model_id)
        if result:
            print(f"✅ Removed '{args.model_id}' from local catalog")
        else:
            print(f"⚠️  Model '{args.model_id}' not found in local catalog")
        return 0
    except CatalogConfigError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1


def cmd_init(args: argparse.Namespace, config: Config) -> int:
    """Initialize local catalog directory."""
    try:
        from inference_djinn.catalog.local_config import (
            SchemaVersionError,
            get_local_catalog_path,
            load_local_catalog,
            save_local_catalog,
        )
        from inference_djinn.catalog.schema import get_catalog_template
    except ImportError:
        print("Error: inference_djinn not available", file=sys.stderr)
        return 1

    catalog_path = get_local_catalog_path()

    if catalog_path.exists() and not args.force:
        print(f"Local catalog already exists at {catalog_path}")
        print("Use --force to reinitialize")
        return 1

    try:
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        if catalog_path.exists():
            catalog = load_local_catalog()
        else:
            catalog = get_catalog_template()
        save_local_catalog(catalog)
        print(f"✅ Initialized local catalog at {catalog_path}")
        return 0
    except SchemaVersionError as e:
        print(f"❌ Schema version error: {e}", file=sys.stderr)
        return 1


def cmd_validate(args: argparse.Namespace, config: Config) -> int:
    """Validate catalog schema."""
    try:
        from inference_djinn.catalog.local_config import (
            get_local_catalog_path,
            load_static_catalog,
        )
        from inference_djinn.catalog.schema import validate_catalog_schema
    except ImportError:
        print("Error: inference_djinn not available", file=sys.stderr)
        return 1

    errors = []

    if args.local or not args.static:
        catalog_path = get_local_catalog_path()
        if catalog_path.exists():
            with open(catalog_path) as f:
                catalog = yaml.safe_load(f)
            result = validate_catalog_schema(catalog)
            if not result.valid:
                errors.append(f"Local catalog: {result.message}")
                print(f"❌ Local catalog: {result.message}")
            else:
                print(f"✅ Local catalog: valid (v{result.found_version})")
        else:
            print(f"⚠️  No local catalog at {catalog_path}")

    if args.static or not args.local:
        static_path = config.catalog_path if config.catalog_path.exists() else None
        try:
            catalog = load_static_catalog(static_path)
            result = validate_catalog_schema(catalog)
            if not result.valid:
                errors.append(f"Static catalog: {result.message}")
                print(f"❌ Static catalog: {result.message}")
            else:
                print("✅ Static catalog: valid")
        except Exception as e:
            errors.append(f"Static catalog: {e}")
            print(f"❌ Static catalog: {e}")

    return 1 if errors else 0


def cmd_update(args: argparse.Namespace, config: Config) -> int:
    """Update model metadata or profiles in local catalog."""
    try:
        from inference_djinn.catalog.local_config import (
            load_local_catalog,
            save_local_catalog,
        )
    except ImportError:
        print("Error: inference_djinn not available", file=sys.stderr)
        return 1

    try:
        catalog = load_local_catalog()
    except Exception as e:
        print(f"❌ Failed to load local catalog: {e}", file=sys.stderr)
        return 1

    models = catalog.get("models", {})
    if not isinstance(models, dict) or args.model_id not in models:
        print(f"❌ Model '{args.model_id}' not in local catalog", file=sys.stderr)
        print(f"   Use 'model-manager export {args.model_id}' first")
        return 1

    entry = models[args.model_id]
    if not isinstance(entry, dict):
        print(f"❌ Invalid model entry for '{args.model_id}'", file=sys.stderr)
        return 1

    modified = False
    modified = _update_vram(args, entry) or modified
    modified = _update_ram(args, entry) or modified
    modified = _update_activated_gpu(args, entry) or modified
    modified = _update_activated_cpu(args, entry) or modified
    modified = _update_metadata(args, entry) or modified

    if modified:
        save_local_catalog(catalog)
        print(f"✅ Updated '{args.model_id}' in local catalog")
    else:
        print("No changes made")

    return 0


def _update_vram(args: argparse.Namespace, entry: dict) -> bool:
    """Update VRAM values (V2: devices structure)."""
    if not args.set_vram:
        return False

    if "devices" not in entry:
        print("❌ Invalid V2 entry: missing 'devices' field", file=sys.stderr)
        return False

    try:
        ctx, vram = args.set_vram.split(":")
        for device_name, device_data in entry.get("devices", {}).items():
            if not isinstance(device_data, dict):
                continue
            if device_name in ("gpu", "hybrid"):
                profiles = device_data.get("profiles", {})
                if isinstance(profiles, dict) and ctx in profiles:
                    profiles[ctx]["vram_mb"] = int(vram)
                    print(f"Updated {device_name} profile {ctx}: vram_mb={vram}")
                    return True
    except ValueError:
        print("❌ Invalid --set-vram format. Use CTX:MB (e.g., 8192:12500)")
    return False


def _update_ram(args: argparse.Namespace, entry: dict) -> bool:
    """Update RAM values (V2: devices structure)."""
    if not args.set_ram:
        return False

    if "devices" not in entry:
        print("❌ Invalid V2 entry: missing 'devices' field", file=sys.stderr)
        return False

    try:
        ctx, ram = args.set_ram.split(":")
        for device_name, device_data in entry.get("devices", {}).items():
            if not isinstance(device_data, dict):
                continue
            profiles = device_data.get("profiles", {})
            if isinstance(profiles, dict) and ctx in profiles:
                profiles[ctx]["ram_mb"] = int(ram)
                print(f"Updated {device_name} profile {ctx}: ram_mb={ram}")
                return True
    except ValueError:
        print("❌ Invalid --set-ram format. Use CTX:MB (e.g., 8192:4000)")
    return False


def _update_activated_gpu(args: argparse.Namespace, entry: dict) -> bool:
    """Update activated GPU contexts."""
    if not args.activate_gpu:
        return False
    try:
        contexts = [int(c) for c in args.activate_gpu.split(",")]
        if "metadata" not in entry:
            entry["metadata"] = {}
        entry["metadata"]["activated_gpu_contexts"] = contexts
        print(f"Set activated_gpu_contexts: {contexts}")
        return True
    except ValueError:
        print("❌ Invalid --activate-gpu format. Use integers (e.g., 4096,8192)")
    return False


def _update_activated_cpu(args: argparse.Namespace, entry: dict) -> bool:
    """Update activated CPU contexts."""
    if not args.activate_cpu:
        return False
    try:
        contexts = [int(c) for c in args.activate_cpu.split(",")]
        if "metadata" not in entry:
            entry["metadata"] = {}
        entry["metadata"]["activated_cpu_contexts"] = contexts
        print(f"Set activated_cpu_contexts: {contexts}")
        return True
    except ValueError:
        print("❌ Invalid --activate-cpu format. Use integers (e.g., 4096,8192)")
    return False


def _update_metadata(args: argparse.Namespace, entry: dict) -> bool:
    """Update arbitrary metadata field."""
    if not args.set_metadata:
        return False
    try:
        key, value = args.set_metadata.split("=", 1)
        if "metadata" not in entry:
            entry["metadata"] = {}
        entry["metadata"][key] = yaml.safe_load(value)
        print(f"Set metadata.{key}={value}")
        return True
    except ValueError:
        print("❌ Invalid --set-metadata format. Use KEY=VALUE")
    return False
