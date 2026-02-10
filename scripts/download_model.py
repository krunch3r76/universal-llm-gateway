#!/usr/bin/env python3
"""Download models using verified_models.json data."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download


def load_verified_models(data_file: Path) -> dict:
    """Load verified models data file."""
    with open(data_file) as f:
        return json.load(f)


def find_model(data: dict, model_id: str) -> dict | None:
    """Find model entry by model_id."""
    for model in data.get("models", []):
        if model.get("model_id") == model_id:
            return model
    return None


def compute_sha256(file_path: Path, chunk_size: int = 8192 * 1024) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            sha256.update(chunk)
    return sha256.hexdigest()


def download_gguf(
    model: dict,
    dest_dir: Path,
    verify: bool = True,
) -> Path | None:
    """Download a GGUF model from HuggingFace.

    Args:
        model: Model entry from verified_models.json
        dest_dir: Destination directory for download
        verify: Whether to verify SHA256 after download

    Returns:
        Path to downloaded file, or None on failure
    """
    download_info = model.get("download", {})
    hf_info = download_info.get("huggingface", {})

    repo_id = hf_info.get("repo")
    filename = hf_info.get("file")
    expected_sha256 = download_info.get("sha256")
    expected_size = download_info.get("size_bytes")

    if not repo_id or not filename:
        print(
            f"Error: Missing repo or file info for {model.get('model_id')}",
            file=sys.stderr,
        )
        return None

    print(f"Downloading: {model.get('model_id')}")
    print(f"  Repo: {repo_id}")
    print(f"  File: {filename}")
    print(f"  Expected size: {expected_size:,} bytes")
    print()

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename

    # Check if already exists
    if dest_path.exists():
        local_size = dest_path.stat().st_size
        if local_size == expected_size:
            print(f"File already exists with correct size: {dest_path}")
            if verify and expected_sha256:
                print("Verifying existing file...")
                local_sha256 = compute_sha256(dest_path)
                if local_sha256 == expected_sha256:
                    print("✅ Existing file verified")
                    return dest_path
                else:
                    print("❌ Hash mismatch - will re-download")
            else:
                return dest_path
        else:
            print(f"Existing file has wrong size ({local_size:,} vs {expected_size:,})")
            print("Will re-download")

    # Download using huggingface_hub
    print(f"Downloading to: {dest_path}")
    try:
        downloaded_path = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=dest_dir,
        )
        downloaded_path = Path(downloaded_path)
    except Exception as e:
        print(f"Error: Download failed: {e}", file=sys.stderr)
        return None

    print(f"Downloaded: {downloaded_path}")

    # Verify size
    actual_size = downloaded_path.stat().st_size
    print(f"Downloaded size: {actual_size:,} bytes")

    if expected_size and actual_size != expected_size:
        print(f"❌ Size mismatch: expected {expected_size:,}, got {actual_size:,}")
        return None

    # Verify SHA256
    if verify and expected_sha256:
        print("Verifying SHA256...")
        actual_sha256 = compute_sha256(downloaded_path)
        print(f"Expected: {expected_sha256}")
        print(f"Actual:   {actual_sha256}")

        if actual_sha256 != expected_sha256:
            print("❌ SHA256 mismatch - file may be corrupted")
            return None

        print("✅ SHA256 verified")

    print(f"\n✅ Successfully downloaded: {downloaded_path}")
    return downloaded_path


def main():
    parser = argparse.ArgumentParser(
        description="Download models from HuggingFace using verified_models.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download a model
  python scripts/download_model.py deepseek-coder-7b-instruct-v1-5-q4-k-m \\
    --data /mnt/torus/models/verified_models.json \\
    --dest /mnt/torus/models

  # List available models
  python scripts/download_model.py --list --data /mnt/torus/models/verified_models.json

  # Download without verification (faster but less safe)
  python scripts/download_model.py cursorcore-qw2-5-1-5b-q4-k-m \\
    --data /mnt/torus/models/verified_models.json \\
    --dest /mnt/torus/models \\
    --no-verify
""",
    )

    parser.add_argument("model_id", nargs="?", help="Model ID to download")
    parser.add_argument(
        "--data",
        "-d",
        type=Path,
        default=Path("/mnt/torus/models/verified_models.json"),
        help="Path to verified_models.json",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path("/mnt/torus/models"),
        help="Destination directory for downloads",
    )
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List available models and exit",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip SHA256 verification after download",
    )

    args = parser.parse_args()

    # Load data file
    if not args.data.exists():
        print(f"Error: Data file not found: {args.data}", file=sys.stderr)
        sys.exit(1)

    data = load_verified_models(args.data)
    models = data.get("models", [])

    # List mode
    if args.list:
        print(f"Available models ({len(models)}):")
        for model in models:
            model_id = model.get("model_id", "unknown")
            fmt = model.get("format", "unknown")
            size = model.get("download", {}).get("size_bytes", 0)
            size_gb = size / (1024**3)
            repo = (
                model.get("download", {}).get("huggingface", {}).get("repo", "unknown")
            )
            print(f"  {model_id}")
            print(f"    Format: {fmt}, Size: {size_gb:.1f} GB")
            print(f"    Repo: {repo}")
        sys.exit(0)

    # Require model_id for download
    if not args.model_id:
        parser.error("model_id is required (use --list to see available models)")

    # Find model
    model = find_model(data, args.model_id)
    if not model:
        print(f"Error: Model not found: {args.model_id}", file=sys.stderr)
        print("Use --list to see available models", file=sys.stderr)
        sys.exit(1)

    # Check format
    fmt = model.get("format", "unknown")
    if fmt != "gguf":
        print(
            f"Error: Only GGUF format supported currently (model is {fmt})",
            file=sys.stderr,
        )
        sys.exit(1)

    # Download
    result = download_gguf(
        model=model,
        dest_dir=args.dest,
        verify=not args.no_verify,
    )

    if result is None:
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
