"""Helper functions for verified registry operations."""

from datetime import UTC, datetime

from ..config import Config
from ..huggingface import get_hf_file_info
from ..models import VerifiedModel
from ..registry import VerifiedRegistry


def add_entries_to_verified_registry(
    entries: dict,
    config: Config,
    network: bool = False,
    force: bool = False,
) -> int:
    """Add generated catalog entries to the verified registry."""
    print("\n📋 Adding to verified registry...")
    for model_id, entry in entries.items():
        result = add_single_to_verified(
            model_id=model_id,
            entry=entry,
            config=config,
            network=network,
            force=force,
        )
        if result != 0:
            return result
    print("✅ Verified registry updated")
    return 0


def add_single_to_verified(
    model_id: str,
    entry: dict,
    config: Config,
    network: bool = False,
    force: bool = False,
) -> int:
    """Add a single catalog entry to the verified registry."""
    download = entry.get("download", {})
    hf = download.get("huggingface", {})
    repo = hf.get("repo")
    file = hf.get("file")
    sha256 = download.get("sha256")
    size_bytes = download.get("size_bytes")
    format_type = entry.get("metadata", {}).get("format", "gguf")

    if not repo or not file:
        print(f"   ⚠️  {model_id}: Missing HF repo/file, skipping verified registry")
        return 0  # Not fatal, just skip

    # Fetch from HF if missing
    if not sha256 or not size_bytes:
        if not network:
            print(
                f"   ⚠️  {model_id}: Missing sha256/size, use --network to fetch from HF"
            )
            return 0
        print(f"   Fetching HF metadata for {model_id}...")
        hf_sha256, hf_size = get_hf_file_info(repo, file)
        if hf_sha256:
            sha256 = hf_sha256
        if hf_size:
            size_bytes = hf_size

    if not sha256 or not size_bytes:
        print(f"   ⚠️  {model_id}: Could not determine sha256/size, skipping")
        return 0

    verified_model = VerifiedModel(
        model_id=model_id,
        local_path=str(config.model_root / file),
        format=format_type,
        repo=repo,
        file=file,
        size_bytes=size_bytes,
        sha256=sha256,
        verified_at=datetime.now(UTC).isoformat(),
    )

    registry = VerifiedRegistry(config.verified_path)
    if registry.exists(model_id) and not force:
        print(f"   ⚠️  {model_id}: Already in verified registry (use --force-verified)")
        return 0

    registry.add(verified_model)
    registry.save()
    print(f"   ✅ {model_id} added to verified registry")
    return 0
