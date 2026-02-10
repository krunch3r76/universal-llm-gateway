"""Configuration and constants for model-manager CLI."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_VERIFIED_PATH = Path("/mnt/torus/models/verified_models.json")
DEFAULT_MODEL_ROOT = Path(os.getenv("MODEL_PATH_ROOT", "/mnt/torus/models"))


def get_default_catalog_path() -> Path:
    """Get default static catalog path using workspace root detection."""
    try:
        from universal_workspace import get_static_catalog_path
        return get_static_catalog_path()
    except (RuntimeError, FileNotFoundError) as e:
        print(f"Error: Cannot locate static catalog: {e}", file=sys.stderr)
        print("Ensure you're running from the workspace root or workspace_root/libs is in PYTHONPATH.", file=sys.stderr)
        sys.exit(1)


@dataclass
class Config:
    """CLI configuration."""

    verified_path: Path = DEFAULT_VERIFIED_PATH
    catalog_path: Path | None = None  # Lazy-initialized via property
    model_root: Path = DEFAULT_MODEL_ROOT
    verbose: bool = False
    
    def __post_init__(self):
        """Initialize catalog_path if not explicitly set."""
        if self.catalog_path is None:
            self.catalog_path = get_default_catalog_path()
