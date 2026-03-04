"""Catalog discovery and listing commands."""
# ruff: noqa: PLC0415

import argparse
import sys
from pathlib import Path

import yaml

from ..api_client import GatewayAPIClient, ModelDetail, get_api_client
from ..config import Config
from ..registry import Catalog, VerifiedRegistry
from ..utils import format_size
from .generate import generate_to_file, generate_via_api
from .verified_helpers import add_entries_to_verified_registry


def cmd_discover(args: argparse.Namespace, config: Config) -> int:
    """Discover uncataloged models in a directory."""
    try:
        from inference_djinn.catalog import ModelDiscovery
    except ImportError:
        print("Error: inference_djinn not available", file=sys.stderr)
        return 1

    scan_path = Path(args.path).expanduser().resolve()
    if not scan_path.exists():
        print(f"Error: Path not found: {scan_path}", file=sys.stderr)
        return 1

    discovery = ModelDiscovery(catalog_path=config.catalog_path)

    print(f"Scanning: {scan_path}")
    print(f"Catalog: {config.catalog_path}")
    print()

    try:
        models = discovery.scan_directory(
            scan_path,
            recursive=not args.no_recursive,
            include_cataloged=args.include_cataloged,
        )
    except Exception as e:
        print(f"Error scanning directory: {e}", file=sys.stderr)
        return 1

    if not models:
        print("No uncataloged models found")
        return 0

    print(f"Found {len(models)} uncataloged model(s):\n")

    for model in models:
        print(f"  {model.model_id}")
        print(f"    Path: {model.path}")
        print(f"    Format: {model.format.value}")
        print(f"    Size: {format_size(model.size_bytes)}")
        print()

    return 0


def cmd_generate(args: argparse.Namespace, config: Config) -> int:
    """Generate catalog entry from model file."""
    stargate_url = getattr(args, "stargate", None)
    output_path = getattr(args, "output", None)

    # Validate mutually exclusive options
    if stargate_url and output_path:
        print(
            "Error: --stargate and --output are mutually exclusive",
            file=sys.stderr,
        )
        return 1

    # Thinking model flag
    thinking = getattr(args, "thinking", False)

    # Validate vision model flags
    mmproj = getattr(args, "mmproj", None)
    vision_arch = getattr(args, "vision_architecture", None)
    tokens_per_image = getattr(args, "tokens_per_image", None)

    if mmproj:
        if not vision_arch:
            print(
                "Error: --vision-architecture required with --mmproj", file=sys.stderr
            )
            return 1
        if tokens_per_image is None:
            print("Error: --tokens-per-image required with --mmproj", file=sys.stderr)
            return 1
        if tokens_per_image <= 0:
            print("Error: --tokens-per-image must be > 0", file=sys.stderr)
            return 1

    try:
        from inference_djinn.catalog import CatalogEntryGenerator, ModelDiscovery
    except ImportError:
        print("Error: inference_djinn not available", file=sys.stderr)
        return 1

    model_path = Path(args.path).expanduser().resolve()
    if not model_path.exists():
        print(f"Error: Path not found: {model_path}", file=sys.stderr)
        return 1

    # Default --file to model filename if not provided
    hf_file = (
        args.file if args.file else (model_path.name if model_path.is_file() else None)
    )

    discovery = ModelDiscovery()
    generator = CatalogEntryGenerator(discovery=discovery)

    print(f"Generating catalog entry for: {model_path}")

    if model_path.is_dir() and not (model_path / "config.json").exists():
        models = discovery.scan_directory(
            model_path, recursive=not getattr(args, "no_recursive", False)
        )
        if not models:
            print("No models found in directory")
            return 1

        print(f"Found {len(models)} model(s)")
        entries = generator.generate_batch(models, trace_source=not args.no_trace)

        # Inject thinking capability into all entries if flagged
        if thinking:
            for entry in entries.values():
                metadata = entry.setdefault("metadata", {})
                capabilities = metadata.setdefault("capabilities", {})
                reasoning = capabilities.setdefault("reasoning", {})
                reasoning["supports_thinking"] = True

        # Inject vision metadata into all entries if provided
        if mmproj and vision_arch and tokens_per_image:
            for entry in entries.values():
                _inject_vision_metadata(entry, mmproj, vision_arch, tokens_per_image)
    else:
        discovered = discovery.scan_single(model_path)
        if not discovered:
            print(f"Error: Could not identify model at: {model_path}", file=sys.stderr)
            return 1

        try:
            entry = generator.generate(
                discovered,
                trace_source=not args.no_trace,
                hf_repo=args.repo,
                hf_file=hf_file,
            )

            # Inject thinking capability if flagged
            if thinking:
                metadata = entry.setdefault("metadata", {})
                capabilities = metadata.setdefault("capabilities", {})
                reasoning = capabilities.setdefault("reasoning", {})
                reasoning["supports_thinking"] = True

            # Inject vision metadata if provided
            if mmproj and vision_arch and tokens_per_image:
                _inject_vision_metadata(entry, mmproj, vision_arch, tokens_per_image)

            entries = {discovered.model_id: entry}
        except Exception as e:
            print(f"Error generating entry: {e}", file=sys.stderr)
            return 1

    # Generate via API or file
    result = 0
    static = getattr(args, "static", False)

    if static or stargate_url:
        # Static writes or API calls (stargate_url can be None for static)
        result = generate_via_api(
            args, entries, stargate_url or "http://localhost:9999", model_path
        )
    elif output_path:
        result = generate_to_file(args, entries, Path(output_path))
    else:
        print(CatalogEntryGenerator.format_yaml(entries))

    if result != 0:
        return result

    # Add to verified registry if requested
    if getattr(args, "add_verified", False):
        # When generating from a single local file, pass path so sha256/size can be computed without --network
        local_paths: dict[str, Path] | None = None
        if len(entries) == 1 and model_path.is_file():
            (mid,) = entries.keys()
            local_paths = {mid: model_path}
        return add_entries_to_verified_registry(
            entries=entries,
            config=config,
            network=getattr(args, "network", False),
            force=getattr(args, "force_verified", False),
            local_path_by_model_id=local_paths,
        )

    return 0


def cmd_list(args: argparse.Namespace, config: Config) -> int:  # noqa: PLR0911
    """List models in catalog or verified registry."""
    if getattr(args, "show_verified", False):
        return _list_verified(config)

    client = get_api_client(
        getattr(args, "gateway", None),
        api_key=getattr(args, "gateway_api_key", None),
        timeout=getattr(args, "timeout", None),
    )

    use_api = (
        client
        and not getattr(args, "local", False)
        and not getattr(args, "static", False)
        and not getattr(args, "merged", False)
    )
    if use_api:
        return _list_from_api(client, args)

    if (
        client is None
        and not getattr(args, "local", False)
        and not getattr(args, "static", False)
        and not getattr(args, "merged", False)
        and getattr(args, "verbose", False)
    ):
        print("Using file-based catalog (Gateway unavailable)", file=sys.stderr)

    try:
        from inference_djinn.catalog.local_config import (
            get_local_catalog_path,
            list_local_models,
            load_local_catalog,
            load_static_catalog,
            merge_catalogs,
        )
    except ImportError:
        return _list_legacy_catalog(config)

    source = "merged"
    model_ids: list[str] = []
    local_ids: set[str] = set()

    if getattr(args, "local", False):
        catalog_path = get_local_catalog_path()
        if not catalog_path.exists():
            print(f"No local catalog at {catalog_path}")
            return 0
        model_ids = list_local_models()
        source = "local"
    elif getattr(args, "static", False):
        static_path = config.catalog_path if config.catalog_path.exists() else None
        try:
            static = load_static_catalog(static_path)
            static_models = static.get("models", {})
            model_ids = (
                list(static_models.keys()) if isinstance(static_models, dict) else []
            )
            source = "static"
        except Exception as e:
            print(f"❌ Failed to load static catalog: {e}", file=sys.stderr)
            return 1
    else:
        static_path = config.catalog_path if config.catalog_path.exists() else None
        try:
            static = load_static_catalog(static_path)
        except Exception as e:
            print(f"❌ Failed to load static catalog: {e}", file=sys.stderr)
            return 1
        try:
            local = load_local_catalog()
            local_models = local.get("models", {})
            local_ids = (
                set(local_models.keys()) if isinstance(local_models, dict) else set()
            )
        except Exception:
            local = {"models": {}}
        merged = merge_catalogs(static, local)
        merged_models = merged.get("models", {})
        model_ids = (
            list(merged_models.keys()) if isinstance(merged_models, dict) else []
        )
        source = "merged"

    print(f"\n{source.title()} catalog ({len(model_ids)} models):\n")
    for model_id in sorted(model_ids):
        is_local = source == "merged" and model_id in local_ids
        local_marker = " (local)" if is_local else ""
        print(f"  • {model_id}{local_marker}")

    return 0


def _list_from_api(client: GatewayAPIClient, args: argparse.Namespace) -> int:
    """List models from Gateway API."""
    format_filter = getattr(args, "format", None)
    try:
        models = client.list_models(format_filter)
    except Exception as exc:  # noqa: BLE001
        print(f"API error: {exc}", file=sys.stderr)
        return 1

    if not models:
        print("No models found")
        return 0

    for m in models:
        if getattr(args, "verbose", False):
            print(f"{m.model_id}")
            print(f"  filename: {m.filename}")
            print(f"  format: {m.format}")
            if m.hf_repo:
                print(f"  repo: {m.hf_repo}")
        else:
            print(f"{m.model_id} ({m.format})")

    print(f"\nTotal: {len(models)} models")
    return 0


def _list_verified(config: Config) -> int:
    """List verified models."""
    registry = VerifiedRegistry(config.verified_path)
    models = registry.list_all()

    if not models:
        print("No verified models")
        return 0

    print(f"Verified models ({len(models)}):")
    for m in models:
        print(f"  {m.model_id}")
        print(f"    Format: {m.format}, Size: {format_size(m.size_bytes)}")
        print(f"    Repo: {m.repo}")
        print(f"    Verified: {m.verified_at}")
    return 0


def _list_legacy_catalog(config: Config) -> int:
    """List models using legacy catalog."""
    catalog = Catalog(config.catalog_path)
    models = catalog.list_all()

    if not models:
        print("No models in catalog")
        return 0

    print(f"Catalog models ({len(models)}):")
    for m in models:
        meta = m.metadata
        size = m.download.get("size_bytes", 0)
        print(f"  {m.model_id}")
        print(f"    Format: {meta.get('format', 'unknown')}")
        print(f"    Size: {format_size(size)}")
    return 0


def cmd_info(args: argparse.Namespace, config: Config) -> int:
    """Show detailed model information."""
    client = get_api_client(
        getattr(args, "gateway", None),
        api_key=getattr(args, "gateway_api_key", None),
        timeout=getattr(args, "timeout", None),
    )

    full = getattr(args, "full", False)

    if client and not getattr(args, "local", False):
        detail = client.get_model(args.model_id)
        if detail:
            return _info_from_api(detail, config, full=full)
        if getattr(args, "verbose", False):
            print(
                "Using file-based catalog (Gateway missing model or unavailable)",
                file=sys.stderr,
            )

    return _info_from_files(args, config, full=full)


def _info_from_api(detail: ModelDetail, config: Config, full: bool = False) -> int:
    """Render model info returned by Gateway API."""
    registry = VerifiedRegistry(config.verified_path)
    ver_model = registry.get(detail.model_id)

    print(f"Model: {detail.model_id}")
    print("=" * 60)

    print("\n[Catalog Entry]")
    print(yaml.dump({"metadata": detail.metadata}, default_flow_style=False))
    print(yaml.dump({"download": detail.download}, default_flow_style=False))

    if full:
        # Show complete devices with loader and profiles (V2)
        if hasattr(detail, "devices") and detail.devices:
            print("devices:")
            print(yaml.dump(detail.devices, default_flow_style=False, indent=2))
        else:
            print("❌ No devices configured (invalid V2 entry)", file=sys.stderr)
            return 1  # Fail fast
    else:
        # V2: show device keys
        if hasattr(detail, "devices") and detail.devices:
            print("Devices:", list(detail.devices.keys()))
        else:
            print("❌ No devices configured (invalid V2 entry)", file=sys.stderr)
            return 1  # Fail fast

    if ver_model:
        print("\n[Verified Registry]")
        print(f"  Local path: {ver_model.local_path}")
        print(f"  Format: {ver_model.format}")
        print(f"  Repo: {ver_model.repo}")
        print(f"  File: {ver_model.file}")
        print(f"  Size: {format_size(ver_model.size_bytes)}")
        print(f"  SHA256: {ver_model.sha256}")
        print(f"  Verified: {ver_model.verified_at}")

    return 0


def _info_from_files(
    args: argparse.Namespace, config: Config, full: bool = False
) -> int:
    """Render model info from file-based catalog."""
    catalog = Catalog(config.catalog_path)
    cat_model = catalog.get(args.model_id)

    registry = VerifiedRegistry(config.verified_path)
    ver_model = registry.get(args.model_id)

    if not cat_model and not ver_model:
        print(f"Error: Model not found: {args.model_id}")
        return 1

    print(f"Model: {args.model_id}")
    print("=" * 60)

    if cat_model:
        print("\n[Catalog Entry]")
        print(yaml.dump({"metadata": cat_model.metadata}, default_flow_style=False))
        print(yaml.dump({"download": cat_model.download}, default_flow_style=False))

        if full:
            # Show complete devices with loader and profiles (V2)
            if hasattr(cat_model, "devices") and cat_model.devices:
                print("devices:")
                print(yaml.dump(cat_model.devices, default_flow_style=False, indent=2))
            else:
                print("❌ No devices configured (invalid V2 entry)", file=sys.stderr)
                return 1  # Fail fast
        else:
            # V2: show device keys
            if hasattr(cat_model, "devices") and cat_model.devices:
                print("Devices:", list(cat_model.devices.keys()))
            else:
                print("❌ No devices configured (invalid V2 entry)", file=sys.stderr)
                return 1  # Fail fast

    if ver_model:
        print("\n[Verified Registry]")
        print(f"  Local path: {ver_model.local_path}")
        print(f"  Format: {ver_model.format}")
        print(f"  Repo: {ver_model.repo}")
        print(f"  File: {ver_model.file}")
        print(f"  Size: {format_size(ver_model.size_bytes)}")
        print(f"  SHA256: {ver_model.sha256}")
        print(f"  Verified: {ver_model.verified_at}")

    return 0


def _inject_vision_metadata(
    entry: dict,
    mmproj_path: str,
    vision_architecture: str,
    tokens_per_image: int,
) -> None:
    """Inject vision model metadata into catalog entry (V4 format).

    Sets capabilities.modalities and adds vision params to loader.
    tokens_per_image stays in loader (engine config, not capability).
    """
    metadata = entry.setdefault("metadata", {})
    capabilities = metadata.setdefault("capabilities", {})
    modalities = capabilities.setdefault("modalities", {})
    modalities["input"] = ["text", "vision"]
    modalities["vision_architecture"] = vision_architecture

    # Update loader (V2: top-level, not configurations.base_loader)
    loader = entry.setdefault("loader", {})
    loader["clip_model_path"] = Path(mmproj_path).name
    loader["vision_architecture"] = vision_architecture
    loader["tokens_per_image"] = tokens_per_image
