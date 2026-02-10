"""Download command for model-manager CLI."""

import argparse
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

from ..config import Config
from ..models import VerifiedModel
from ..registry import VerifiedRegistry
from ..utils import compute_sha256, format_size
from .network_check import check_network_flag


def cmd_download(args: argparse.Namespace, config: Config) -> int:
    """Download model from verified registry."""
    if (exit_code := check_network_flag(args, "download")) is not None:
        return exit_code

    registry = VerifiedRegistry(config.verified_path)
    model = registry.get(args.model_id)

    error = _validate_download_model(model, args.model_id)
    if error:
        print(error, file=sys.stderr)
        return 1

    assert model is not None and model.file is not None
    dest_dir = args.dest or config.model_root
    dest_path = dest_dir / model.file
    _print_download_info(model, dest_path)

    if dest_path.exists():
        result = _check_existing_file(dest_path, model, args.no_verify)
        if result is not None:
            return result

    return _perform_download(model, dest_dir, args.no_verify)


def _validate_download_model(model: VerifiedModel | None, model_id: str) -> str | None:
    """Validate model can be downloaded. Returns error message or None."""
    if not model:
        return (
            f"Error: Model not found: {model_id}\nUse 'model-manager list --verified'"
        )
    if model.format != "gguf":
        return f"Error: Only GGUF downloads supported (model is {model.format})"
    if not model.file:
        return "Error: Model has no file specified"
    return None


def _print_download_info(model: VerifiedModel, dest_path: Path) -> None:
    """Print download information."""
    print(f"Downloading: {model.model_id}")
    print(f"  Repo: {model.repo}")
    print(f"  File: {model.file}")
    print(f"  Size: {format_size(model.size_bytes)}")
    print(f"  Dest: {dest_path}")
    print()


def _check_existing_file(
    dest_path: Path, model: VerifiedModel, no_verify: bool
) -> int | None:
    """Check existing file. Returns exit code if done, None to continue."""
    if dest_path.stat().st_size != model.size_bytes:
        return None
    print("File already exists with correct size")
    if no_verify or not model.sha256:
        return 0
    print("Verifying existing file...")
    if compute_sha256(dest_path) == model.sha256:
        print("✅ Verified")
        return 0
    print("❌ Hash mismatch - will re-download")
    return None


def _perform_download(model: VerifiedModel, dest_dir: Path, no_verify: bool) -> int:
    """Perform the actual download and verification."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        downloaded_path = hf_hub_download(
            repo_id=model.repo,
            filename=model.file,
            local_dir=str(dest_dir),
        )
    except Exception as e:
        print(f"Error: Download failed: {e}", file=sys.stderr)
        return 1

    print(f"Downloaded: {downloaded_path}")
    if not no_verify and model.sha256:
        print("Verifying SHA256...")
        if compute_sha256(Path(downloaded_path)) != model.sha256:
            print("❌ SHA256 mismatch - file may be corrupted")
            return 1
        print("✅ Verified")

    print(f"\n✅ Successfully downloaded: {downloaded_path}")
    return 0
