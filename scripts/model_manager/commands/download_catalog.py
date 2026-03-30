"""Download models from static catalog."""

import argparse
import os
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

try:
    from huggingface_hub import login
except ImportError:
    # Fallback for older versions
    login = None

from ..config import Config
from ..registry import Catalog
from ..utils import format_size
from .network_check import check_network_flag


def cmd_download_from_catalog(args: argparse.Namespace, config: Config) -> int:
    """Download model from static catalog."""
    if (exit_code := check_network_flag(args, "download-from-catalog")) is not None:
        return exit_code

    catalog = Catalog(config.catalog_path)
    model = catalog.get(args.model_id)

    if not model:
        print(f"Error: Model not found in catalog: {args.model_id}", file=sys.stderr)
        print("Use 'model-manager list' to see available models", file=sys.stderr)
        return 1

    # Extract download info from catalog
    download_info = model.download.get("huggingface", {})
    repo = download_info.get("repo")
    file = download_info.get("file")
    local_subdir = download_info.get("local_subdir")
    format_type = model.metadata.get("format", "gguf")

    if not repo:
        print(f"Error: Model {args.model_id} has no HuggingFace repo in catalog", file=sys.stderr)
        return 1

    dest_dir = args.dest or config.model_root
    dest_dir = Path(dest_dir).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Handle authentication for gated models
    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
    if hf_token and login:
        try:
            login(token=hf_token)
        except Exception as e:
            print(f"Warning: Could not login to HuggingFace: {e}", file=sys.stderr)
            print("Continuing without explicit login (may fail for gated models)", file=sys.stderr)

    # Download based on format
    if format_type in ("hf", "awq", "gptq", "cross-encoder"):
        # Directory-based download (vLLM and cross-encoder models)
        return _download_directory_model(
            model_id=args.model_id,
            repo=repo,
            dest_dir=dest_dir,
            filename=local_subdir or model.metadata.get("name") or args.model_id,
            ignore_patterns=args.ignore_patterns,
            format_label=(
                "cross-encoder"
                if format_type == "cross-encoder"
                else "vLLM (directory-based)"
            ),
        )
    elif format_type == "gguf":
        # Single file download (GGUF models)
        if not file:
            print(
                f"Error: Model {args.model_id} is GGUF format but has no file specified in catalog",
                file=sys.stderr,
            )
            return 1
        return _download_gguf_model(
            model_id=args.model_id,
            repo=repo,
            file=file,
            dest_dir=dest_dir,
        )
    else:
        print(
            f"Error: Unsupported format '{format_type}' for model {args.model_id}",
            file=sys.stderr,
        )
        print(
            "Supported formats: gguf, hf, awq, gptq, cross-encoder",
            file=sys.stderr,
        )
        return 1


def _download_gguf_model(
    model_id: str, repo: str, file: str, dest_dir: Path
) -> int:
    """Download single GGUF file."""
    dest_path = dest_dir / file

    print(f"Downloading: {model_id}")
    print("  Format: GGUF")
    print(f"  Repo: {repo}")
    print(f"  File: {file}")
    print(f"  Dest: {dest_path}")
    print()

    # Check if file already exists
    if dest_path.exists():
        size = dest_path.stat().st_size
        print(f"File already exists ({format_size(size)})")
        response = input("Re-download? [y/N]: ").strip().lower()
        if response not in ("y", "yes"):
            print("Skipped")
            return 0

    try:
        downloaded_path = hf_hub_download(
            repo_id=repo,
            filename=file,
            local_dir=str(dest_dir),
        )
        print(f"\n✅ Successfully downloaded: {downloaded_path}")
        return 0
    except Exception as e:
        print(f"Error: Download failed: {e}", file=sys.stderr)
        if "401" in str(e) or "Unauthorized" in str(e):
            print(
                "\nThis model may be gated. You need to:",
                file=sys.stderr,
            )
            print("  1. Accept the license on HuggingFace", file=sys.stderr)
            print("  2. Get a token: https://huggingface.co/settings/tokens", file=sys.stderr)
            print("  3. Set HF_TOKEN environment variable or run: huggingface-cli login", file=sys.stderr)
        return 1


def _download_directory_model(
    model_id: str,
    repo: str,
    dest_dir: Path,
    filename: str,
    ignore_patterns: list[str] | None = None,
    format_label: str = "vLLM (directory-based)",
) -> int:
    """Download directory-based model (vLLM or cross-encoder)."""
    target_dir = dest_dir / filename

    print(f"Downloading: {model_id}")
    print(f"  Format: {format_label}")
    print(f"  Repo: {repo}")
    print(f"  Dest: {target_dir}")
    print()

    # Check if directory already exists
    if target_dir.exists() and any(target_dir.iterdir()):
        print(f"Directory already exists: {target_dir}")
        response = input("Re-download? [y/N]: ").strip().lower()
        if response not in ("y", "yes"):
            print("Skipped")
            return 0
        # Remove existing directory
        import shutil
        shutil.rmtree(target_dir)

    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        path = snapshot_download(
            repo_id=repo,
            local_dir=str(target_dir),
            ignore_patterns=ignore_patterns or ["*.md", "*.txt"],
        )
        print(f"\n✅ Successfully downloaded: {path}")
        return 0
    except Exception as e:
        print(f"Error: Download failed: {e}", file=sys.stderr)
        if "401" in str(e) or "Unauthorized" in str(e) or "GatedRepoError" in str(type(e).__name__):
            print(
                "\nThis model is gated. You need to:",
                file=sys.stderr,
            )
            print("  1. Accept the license on HuggingFace", file=sys.stderr)
            print(f"  2. Visit: https://huggingface.co/{repo}", file=sys.stderr)
            print("  3. Get a token: https://huggingface.co/settings/tokens", file=sys.stderr)
            print("  4. Set HF_TOKEN environment variable or run: huggingface-cli login", file=sys.stderr)
        return 1

