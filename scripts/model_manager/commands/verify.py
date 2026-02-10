"""Verify command for model-manager CLI."""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub.errors import HfHubHTTPError

from ..config import Config
from ..huggingface import detect_hf_format, get_hf_file_info
from ..models import VerifiedModel
from ..registry import VerifiedRegistry
from ..utils import compute_sha256, format_size, generate_model_id
from .network_check import check_network_flag


def cmd_verify(args: argparse.Namespace, config: Config) -> int:
    """Verify model origin against HuggingFace."""
    if (exit_code := check_network_flag(args, "verify")) is not None:
        return exit_code

    path = Path(args.path).expanduser().resolve()

    if not path.exists():
        print(f"Error: Path not found: {path}", file=sys.stderr)
        return 1

    if path.is_file():
        filename = args.file if args.file else path.name
        return _verify_gguf(path, args.repo, filename, config)
    return _verify_hf_dir(path, args.repo, config)


def _verify_gguf(local_path: Path, repo_id: str, filename: str, config: Config) -> int:
    """Verify a GGUF file against HuggingFace."""
    print(f"Local file: {local_path}")
    print(f"HF repo: {repo_id}")
    print(f"HF file: {filename}")
    print()

    hf_sha256, hf_size = get_hf_file_info(repo_id, filename)
    if hf_sha256 is None:
        return 1

    local_size = local_path.stat().st_size
    print(f"Local size: {local_size:,} bytes ({format_size(local_size)})")
    if hf_size:
        print(f"HF size:    {hf_size:,} bytes ({format_size(hf_size)})")

    if hf_size and local_size != hf_size:
        print("\n❌ SIZE MISMATCH - skipping hash computation")
        return 1

    print("\nComputing SHA256 of local file...")
    local_sha256 = compute_sha256(local_path)

    print(f"Local SHA256: {local_sha256}")
    print(f"HF SHA256:    {hf_sha256}")

    if local_sha256 != hf_sha256:
        print("\n❌ MISMATCH - Hashes do not match")
        return 1

    print(f"\n✅ MATCH - File verified as originating from {repo_id}")

    registry = VerifiedRegistry(config.verified_path)
    model = VerifiedModel(
        model_id=generate_model_id(filename),
        local_path=str(local_path),
        format="gguf",
        repo=repo_id,
        file=filename,
        size_bytes=local_size,
        sha256=local_sha256,
        verified_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    if registry.exists(model.model_id):
        print(f"\nModel {model.model_id} already in registry, updating...")
    else:
        print(f"\nAdding {model.model_id} to registry...")

    registry.add(model)
    registry.save()
    print(f"Saved to {config.verified_path}")

    return 0


def _verify_hf_dir(local_path: Path, repo_id: str, config: Config) -> int:
    """Verify a HuggingFace model directory."""
    print(f"Local directory: {local_path}")
    print(f"HF repo: {repo_id}")
    print()

    hf_files = _fetch_hf_lfs_files(repo_id)
    if hf_files is None:
        return 1

    matched, total_checked, total_size, largest_sha256 = _check_local_files(
        local_path, hf_files
    )

    if total_checked == 0:
        print("No LFS files found to verify")
        return 1

    print(f"\nSummary: {matched}/{total_checked} files verified")
    if matched != total_checked:
        return 1

    _save_hf_model_to_registry(config, local_path, repo_id, total_size, largest_sha256)
    return 0


def _fetch_hf_lfs_files(repo_id: str) -> dict | None:
    """Fetch LFS file info from HuggingFace repo."""
    api = HfApi()
    try:
        repo_info = api.repo_info(repo_id, files_metadata=True)
    except HfHubHTTPError as e:
        print(f"Error: Failed to fetch repo info: {e}", file=sys.stderr)
        return None
    siblings = repo_info.siblings or []
    return {s.rfilename: s for s in siblings if s.lfs}


def _check_local_files(
    local_path: Path, hf_files: dict
) -> tuple[int, int, int, str | None]:
    """Check local files against HuggingFace. Returns (matched, checked, size, sha)."""
    matched, total_checked, total_size = 0, 0, 0
    largest_sha256, largest_size = None, 0

    for local_file in sorted(f for f in local_path.rglob("*") if f.is_file()):
        rel_path = local_file.relative_to(local_path).as_posix()
        sibling = hf_files.get(rel_path)
        if not sibling or not sibling.lfs:
            continue

        total_checked += 1
        local_size = local_file.stat().st_size
        total_size += local_size
        print(f"Checking: {rel_path}")

        if local_size != sibling.lfs.size:
            print(f"  ❌ Size mismatch: local={local_size:,}, HF={sibling.lfs.size:,}")
            continue

        local_sha256 = compute_sha256(local_file)
        if local_sha256 == sibling.lfs.sha256:
            print("  ✅ Match")
            matched += 1
            if local_size > largest_size:
                largest_size, largest_sha256 = local_size, local_sha256
        else:
            print("  ❌ Hash mismatch")

    return matched, total_checked, total_size, largest_sha256


def _save_hf_model_to_registry(
    config: Config, local_path: Path, repo_id: str, total_size: int, sha256: str | None
) -> None:
    """Save verified HF model to registry."""
    model_format = detect_hf_format(local_path)
    registry = VerifiedRegistry(config.verified_path)
    model = VerifiedModel(
        model_id=generate_model_id(local_path.name),
        local_path=str(local_path),
        format=model_format,
        repo=repo_id,
        file=None,
        size_bytes=total_size,
        sha256=sha256,
        verified_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    registry.add(model)
    registry.save()
    print(f"\n✅ Added {model.model_id} to {config.verified_path}")
