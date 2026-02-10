"""Utility functions for model-manager CLI."""

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


def compute_sha256(file_path: Path, chunk_size: int = 8192 * 1024) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            sha256.update(chunk)
    return sha256.hexdigest()


def generate_model_id(filename: str) -> str:
    """Generate model_id from filename."""
    name = Path(filename).stem
    model_id = name.lower().replace(".", "-").replace("_", "-")
    while "--" in model_id:
        model_id = model_id.replace("--", "-")
    return model_id


def format_size(size_bytes: int) -> str:
    """Format byte size as human-readable string."""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def load_json(path: Path) -> dict[str, Any]:
    """Load JSON file."""
    with open(path) as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    """Save JSON file with pretty printing."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML file."""
    with open(path) as f:
        return yaml.safe_load(f)


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    """Save YAML file."""
    with open(path, "w") as f:
        yaml.dump(
            data, f, default_flow_style=False, sort_keys=False, allow_unicode=True
        )
