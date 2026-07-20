"""Model ID to filesystem path resolution for catalog-backed measurement jobs.

Resolves GGUF files and HuggingFace directory layouts from catalog download metadata
with pattern-based fallback search under MODEL_PATH_ROOT.
"""

import os
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)


def resolve_model_path(model_id: str) -> Path | None:
    """Resolve model ID to file path (GGUF) or directory (vLLM/HF)."""
    try:
        from ....core.catalog import get_catalog_loader

        loader = get_catalog_loader()
        model = loader.get_model(model_id)

        if model:
            download = model.get("download", {})
            hf_info = download.get("huggingface", {})
            filename = hf_info.get("file")
            local_subdir = hf_info.get("local_subdir", "")

            model_root = Path(os.environ.get("MODEL_PATH_ROOT", "/mnt/torus/models"))

            if filename:
                path = (
                    model_root / local_subdir / filename
                    if local_subdir
                    else model_root / filename
                )
                if path.exists():
                    return path
            else:
                if local_subdir:
                    path = model_root / local_subdir
                    if path.exists() and path.is_dir():
                        return path
                repo = hf_info.get("repo")
                if repo:
                    dir_name = repo.split("/")[-1] if "/" in repo else repo
                    path = model_root / dir_name
                    if path.exists() and path.is_dir():
                        return path
    except Exception as e:
        logger.warning(
            "Catalog lookup failed for model '%s': %s", model_id, e, exc_info=True
        )

    return _find_model_by_pattern(model_id)


def _find_model_by_pattern(model_id: str) -> Path | None:
    """Find model file by common naming patterns."""
    model_root = Path(os.environ.get("MODEL_PATH_ROOT", "/mnt/torus/models"))

    patterns = [
        f"{model_id}.gguf",
        f"{model_id.replace('-', '_')}.gguf",
        model_id,
    ]

    for pattern in patterns:
        path = model_root / pattern
        if path.exists():
            return path

    return None
