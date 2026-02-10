"""Promote-to-verified command for model-manager CLI."""

import argparse
import sys
from datetime import UTC, datetime

from ..api_client import get_api_client
from ..config import Config
from ..huggingface import get_hf_file_info
from ..models import VerifiedModel
from ..registry import VerifiedRegistry
from .network_check import check_network_flag


def cmd_promote_to_verified(args: argparse.Namespace, config: Config) -> int:
    """Promote a catalog model to the verified registry."""
    # Privacy gate: require --network for HF lookups if refresh requested
    if getattr(args, "refresh_hf", False):
        if (exit_code := check_network_flag(args, "promote")) is not None:
            return exit_code

    # Fetch catalog entry via Gateway API
    client = get_api_client(
        getattr(args, "gateway", None),
        api_key=getattr(args, "gateway_api_key", None),
        timeout=getattr(args, "timeout", None),
    )
    if not client:
        print("Error: Gateway not reachable", file=sys.stderr)
        return 1

    model = client.get_model(args.model_id)
    if not model:
        print(f"Error: Model not found in catalog: {args.model_id}", file=sys.stderr)
        return 1

    # Extract download info from catalog entry
    download = model.download or {}
    hf = download.get("huggingface", {})
    repo = hf.get("repo")
    file = hf.get("file")
    sha256 = download.get("sha256")
    size_bytes = download.get("size_bytes")
    format_type = model.metadata.get("format", "gguf")

    if not repo or not file:
        print(
            "Error: Model missing HuggingFace repo/file in download info",
            file=sys.stderr,
        )
        return 1

    # Refresh from HF if requested or if missing sha256/size
    if getattr(args, "refresh_hf", False) or not sha256 or not size_bytes:
        if not getattr(args, "network", False):
            print("Error: --network required to fetch HF metadata", file=sys.stderr)
            return 1
        print(f"Fetching metadata from HuggingFace: {repo}/{file}")
        hf_sha256, hf_size = get_hf_file_info(repo, file)
        if hf_sha256:
            sha256 = hf_sha256
        if hf_size:
            size_bytes = hf_size

    if not sha256 or not size_bytes:
        print("Error: Could not determine sha256/size_bytes", file=sys.stderr)
        return 1

    # Build verified model entry
    verified_model = VerifiedModel(
        model_id=args.model_id,
        local_path=str(config.model_root / file),
        format=format_type,
        repo=repo,
        file=file,
        size_bytes=size_bytes,
        sha256=sha256,
        verified_at=datetime.now(UTC).isoformat(),
    )

    # Write to registry
    registry = VerifiedRegistry(config.verified_path)
    if registry.exists(args.model_id) and not getattr(args, "force", False):
        print(
            f"Error: {args.model_id} already in verified registry (use --force)",
            file=sys.stderr,
        )
        return 1

    registry.add(verified_model)
    registry.save()
    print(f"✅ Added {args.model_id} to verified registry: {config.verified_path}")
    return 0
