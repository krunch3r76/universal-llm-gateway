"""HuggingFace operations for model-manager CLI."""

import json
import sys
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi
from huggingface_hub.errors import HfHubHTTPError


def get_hf_file_info(repo_id: str, filename: str) -> tuple[str | None, int | None]:
    """Query HuggingFace API for file SHA256 and size."""
    api = HfApi()
    try:
        repo_info = api.repo_info(repo_id, files_metadata=True)
    except HfHubHTTPError as e:
        print(f"Error: Failed to fetch repo info for '{repo_id}': {e}", file=sys.stderr)
        return None, None

    siblings = repo_info.siblings or []
    for sibling in siblings:
        if sibling.rfilename == filename:
            if sibling.lfs:
                return sibling.lfs.sha256, sibling.lfs.size
            print(
                f"Warning: File '{filename}' exists but is not LFS-tracked",
                file=sys.stderr,
            )
            return None, None

    print(f"Error: File '{filename}' not found in repo '{repo_id}'", file=sys.stderr)
    return None, None


def detect_hf_format(local_path: Path) -> str:
    """Detect model format from HuggingFace directory structure."""
    config_path = local_path / "config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
            quant_method = (
                config.get("quantization_config", {}).get("quant_method", "").lower()
            )
            if quant_method == "awq":
                return "awq"
            if quant_method == "gptq":
                return "gptq"
        except (json.JSONDecodeError, KeyError):
            pass
    return "hf"


def extract_metadata(model_path: Path, format_type: str = "gguf") -> dict[str, Any]:
    """
    Extract metadata from model file using inference_djinn.catalog.

    Returns dict with: arch, parameters_m, training_context_length, input_schema, etc.
    """
    try:
        from inference_djinn.catalog import MetadataExtractor

        extractor = MetadataExtractor()
        metadata = extractor.extract(model_path, format_type)
        return metadata.to_catalog_metadata()
    except ImportError:
        print(
            "Warning: inference_djinn not available, skipping metadata extraction",
            file=sys.stderr,
        )
        return {}
    except Exception as e:
        print(f"Warning: Failed to extract metadata: {e}", file=sys.stderr)
        return {}
